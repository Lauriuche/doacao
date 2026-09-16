import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from urllib.parse import urlencode

import requests
from flask import Flask, jsonify, redirect, request
from flask_cors import CORS
import firebase_admin
from firebase_admin import auth as firebase_auth, credentials, db
from cryptography.fernet import Fernet

app = Flask(__name__)
CORS(app, origins=os.getenv('FRONTEND_URL', '*').split(','), supports_credentials=False)

MP_AUTH_URL = 'https://auth.mercadopago.com.br/authorization'
MP_API_URL = 'https://api.mercadopago.com'
FRONTEND_URL = os.environ['FRONTEND_URL'].rstrip('/')
MP_CLIENT_ID = os.environ['MERCADOPAGO_CLIENT_ID']
MP_CLIENT_SECRET = os.environ['MERCADOPAGO_CLIENT_SECRET']
MP_REDIRECT_URI = os.environ['MERCADOPAGO_REDIRECT_URI']
MP_WEBHOOK_URL = os.environ['MERCADOPAGO_WEBHOOK_URL']
STATE_SECRET = os.environ['OAUTH_STATE_SECRET'].encode()
TOKEN_KEY = os.environ['TOKEN_ENCRYPTION_KEY'].encode()
fernet = Fernet(TOKEN_KEY)

if not firebase_admin._apps:
    service_account = json.loads(os.environ['FIREBASE_SERVICE_ACCOUNT_JSON'])
    firebase_admin.initialize_app(credentials.Certificate(service_account), {
        'databaseURL': os.environ['FIREBASE_DATABASE_URL']
    })


def encode_state(uid: str) -> str:
    payload = {'uid': uid, 'nonce': uuid.uuid4().hex, 'exp': int(time.time()) + 600}
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode()
    signature = hmac.new(STATE_SECRET, raw.encode(), hashlib.sha256).hexdigest()
    return f'{raw}.{signature}'


def decode_state(state: str) -> dict:
    raw, signature = state.rsplit('.', 1)
    expected = hmac.new(STATE_SECRET, raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError('invalid state')
    payload = json.loads(base64.urlsafe_b64decode(raw.encode()))
    if payload['exp'] < time.time():
        raise ValueError('expired state')
    return payload


def bearer_uid():
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer '):
        raise PermissionError('missing bearer token')
    decoded = firebase_auth.verify_id_token(header[7:])
    return decoded['uid']


def encrypted(value: str) -> str:
    return fernet.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return fernet.decrypt(value.encode()).decode()


@app.get('/health')
def health():
    return jsonify({'ok': True, 'service': 'doacao-plus-mercadopago'})


@app.get('/api/mercadopago/oauth/start')
def oauth_start():
    try:
        uid = bearer_uid()
        state = encode_state(uid)
        db.reference(f'users/{uid}/paymentConnections/mercadoPago').update({
            'status': 'pending', 'updatedAt': int(time.time() * 1000)
        })
        params = {
            'client_id': MP_CLIENT_ID,
            'response_type': 'code',
            'platform_id': 'mp',
            'redirect_uri': MP_REDIRECT_URI,
            'state': state,
        }
        return jsonify({'authorization_url': MP_AUTH_URL + '?' + urlencode(params), 'state': state})
    except PermissionError as exc:
        return jsonify({'error': str(exc)}), 401
    except Exception:
        app.logger.exception('oauth start failed')
        return jsonify({'error': 'oauth_start_failed'}), 500


@app.get('/api/mercadopago/oauth/callback')
def oauth_callback():
    code = request.args.get('code')
    state = request.args.get('state')
    if not code or not state:
        return redirect(FRONTEND_URL + '/?mp=error&reason=missing_callback')
    try:
        payload = decode_state(state)
        response = requests.post(f'{MP_API_URL}/oauth/token', json={
            'client_id': MP_CLIENT_ID,
            'client_secret': MP_CLIENT_SECRET,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': MP_REDIRECT_URI,
        }, timeout=20)
        response.raise_for_status()
        token_data = response.json()
        uid = payload['uid']
        record = {
            'status': 'connected',
            'userId': token_data.get('user_id'),
            'publicKey': token_data.get('public_key'),
            'email': token_data.get('user_id'),
            'connectedAt': int(time.time() * 1000),
            'updatedAt': int(time.time() * 1000),
        }
        db.reference(f'users/{uid}/paymentConnections/mercadoPago').set(record)
        db.reference(f'_private/mercadoPago/{uid}').set({
            'accessToken': encrypted(token_data['access_token']),
            'refreshToken': encrypted(token_data.get('refresh_token', '')),
            'expiresIn': token_data.get('expires_in'),
            'updatedAt': int(time.time() * 1000),
        })
        return redirect(FRONTEND_URL + '/?mp=connected&state=' + state)
    except Exception:
        app.logger.exception('oauth callback failed')
        return redirect(FRONTEND_URL + '/?mp=error&reason=oauth_exchange')


@app.post('/api/mercadopago/checkout')
def create_checkout():
    try:
        uid = bearer_uid()
        payload = request.get_json(force=True)
        campaign_id = str(payload.get('campaignId', ''))
        value = float(payload.get('value', 0))
        if not campaign_id or value <= 0:
            return jsonify({'error': 'invalid_campaign_or_value'}), 400
        campaign_snap = db.reference(f'campaigns/{campaign_id}').get()
        if not campaign_snap or campaign_snap.get('status') == 'paused':
            return jsonify({'error': 'campaign_unavailable'}), 404
        owner_uid = campaign_snap.get('userId')
        connection = db.reference(f'users/{owner_uid}/paymentConnections/mercadoPago').get() or {}
        if connection.get('status') != 'connected':
            return jsonify({'error': 'seller_not_connected'}), 409
        private = db.reference(f'_private/mercadoPago/{owner_uid}').get() or {}
        access_token = decrypt(private['accessToken'])
        donation_ref = db.reference(f'campaigns/{campaign_id}/donations').push()
        donation_id = donation_ref.key
        external_reference = f'{campaign_id}:{donation_id}'
        donation_ref.set({
            'name': 'Doador', 'value': value, 'method': 'mercado_pago',
            'status': 'pendente', 'paymentProvider': 'mercado_pago',
            'externalReference': external_reference, 'date': int(time.time() * 1000),
        })
        preference = {
            'items': [{'id': campaign_id, 'title': campaign_snap.get('title', 'Doação'), 'quantity': 1, 'currency_id': 'BRL', 'unit_price': value}],
            'external_reference': external_reference,
            'notification_url': MP_WEBHOOK_URL,
            'back_urls': {'success': payload.get('returnUrl', FRONTEND_URL), 'pending': payload.get('returnUrl', FRONTEND_URL), 'failure': payload.get('returnUrl', FRONTEND_URL)},
            'auto_return': 'approved',
        }
        response = requests.post(f'{MP_API_URL}/checkout/preferences', headers={'Authorization': f'Bearer {access_token}'}, json=preference, timeout=20)
        response.raise_for_status()
        data = response.json()
        donation_ref.update({'preferenceId': data.get('id')})
        return jsonify({'init_point': data.get('init_point'), 'donationId': donation_id})
    except PermissionError as exc:
        return jsonify({'error': str(exc)}), 401
    except Exception:
        app.logger.exception('checkout failed')
        return jsonify({'error': 'checkout_failed'}), 500


@app.post('/api/mercadopago/webhook')
def mercadopago_webhook():
    body = request.get_json(silent=True) or {}
    payment_id = (body.get('data') or {}).get('id') or request.args.get('data.id')
    if not payment_id:
        return '', 200
    # Production: validate x-signature with MP_WEBHOOK_SECRET before processing.
    # The payment must be fetched from Mercado Pago, never trusted from the webhook body.
    app.logger.info('Mercado Pago payment notification received: %s', payment_id)
    return '', 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '10000')))
