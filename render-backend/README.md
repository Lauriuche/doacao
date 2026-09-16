# Doação+ API — Render + Mercado Pago

Backend Flask para OAuth por criador, criação de Checkout Pro e webhook. O front-end continua hospedado no GitHub Pages.

## Endpoints

- `GET /health`
- `GET /api/mercadopago/oauth/start` — exige `Authorization: Bearer <Firebase ID token>`.
- `GET /api/mercadopago/oauth/callback`
- `POST /api/mercadopago/checkout` — exige `Authorization: Bearer <Firebase ID token>`.
- `POST /api/mercadopago/webhook`

## Publicar no Render

1. Crie um repositório GitHub e coloque `app.py`, `requirements.txt` e `render.yaml` em uma pasta própria ou na raiz.
2. No Render, crie um **Web Service** conectado ao repositório.
3. Use `pip install -r requirements.txt` como build command.
4. Use `gunicorn --bind 0.0.0.0:$PORT app:app` como start command.
5. Copie a URL pública do serviço, por exemplo `https://doacao-plus-api.onrender.com`.
6. Configure as variáveis de ambiente abaixo.

## Variáveis de ambiente

```text
FRONTEND_URL=https://lauriuche.github.io
MERCADOPAGO_CLIENT_ID=...
MERCADOPAGO_CLIENT_SECRET=...
MERCADOPAGO_REDIRECT_URI=https://doacao-plus-api.onrender.com/api/mercadopago/oauth/callback
MERCADOPAGO_WEBHOOK_URL=https://doacao-plus-api.onrender.com/api/mercadopago/webhook
OAUTH_STATE_SECRET=<segredo longo e aleatório>
TOKEN_ENCRYPTION_KEY=<chave Fernet base64 de 32 bytes>
FIREBASE_DATABASE_URL=https://SEU-PROJETO-default-rtdb.firebaseio.com
FIREBASE_SERVICE_ACCOUNT_JSON=<JSON inteiro da conta de serviço em uma linha>
```

Gere `TOKEN_ENCRYPTION_KEY` com:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Gere `OAUTH_STATE_SECRET` com:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Mercado Pago

No painel de desenvolvedores, configure o Redirect URI exatamente como:

```text
https://doacao-plus-api.onrender.com/api/mercadopago/oauth/callback
```

Configure o webhook de pagamentos como:

```text
https://doacao-plus-api.onrender.com/api/mercadopago/webhook
```

A aplicação deve usar Marketplace/OAuth. O `CLIENT_SECRET`, os tokens dos criadores e a chave de criptografia ficam somente no Render.

## Firebase

A conta de serviço precisa ter permissão para ler e escrever no Realtime Database. O backend grava tokens privados em:

```text
_private/mercadoPago/{uid}
```

As regras do Realtime Database devem impedir que o cliente público leia `_private`.

## Ajuste do front-end

Defina antes do script principal do HTML, ou altere a constante equivalente:

```html
<script>
  window.DOACAO_API_BASE = 'https://doacao-plus-api.onrender.com';
</script>
```

A versão atual deve usar:

```js
const API_BASE = window.DOACAO_API_BASE;
```

E chamar:

```text
${API_BASE}/api/mercadopago/oauth/start
${API_BASE}/api/mercadopago/checkout
```

## Atenção sobre webhook

O endpoint deve validar a assinatura `x-signature` usando o segredo de webhook configurado no Mercado Pago e consultar `GET /v1/payments/{id}` antes de atualizar a doação. Esta base retorna `200` e registra o evento, mas a confirmação de pagamento e a validação da assinatura precisam ser finalizadas antes de produção.
