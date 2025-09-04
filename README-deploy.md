# Deploying Lunara Bot (Render / Docker)

This file explains how to prepare and deploy the Lunara Bot to Render.com or run locally with Docker / docker-compose.

1) Required environment variables

- TELEGRAM_BOT_TOKEN - Telegram bot token
- ADMIN_USER_ID - Your Telegram user id (integer)
- REDIS_URL - Redis connection string (e.g. redis://:<pw>@host:6379/0) - Render can provide a managed Redis
- SLIP_ENCRYPTION_KEY - Fernet key for slip encryption (see below to generate)
- WEBHOOK_HMAC_SECRET - HMAC secret for verifying incoming webhooks
- ENABLE_AUTOTRADE - true/false (optional toggle for autotrade)

2) Generate a Fernet key for `SLIP_ENCRYPTION_KEY`

Run this locally and copy the output into your Render environment or secrets store:

```powershell
python - <<'PY'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
PY
```

3) Deploying to Render

- Create a new Web Service (or Background Worker) on Render.
- Connect your GitHub repo and point Render to the `main` branch (or a deployment branch).
- Set the start command to:

```
python -m src.main
```

- Set the environment variables in the Render dashboard using the list above.
- If you use Render's managed Redis, set `REDIS_URL` to the provided URL.
- Optionally configure a Health Check to hit `/health` (200 expected) if you implement the endpoint.

4) Local Docker usage

- Build the image:

```powershell
docker build -t lunara-bot:local .
```

- Start with docker-compose (Redis + app):

```powershell
docker-compose up --build
```

5) CI / GitHub Actions

- The repo contains `.github/workflows/ci.yml` which installs dependencies and runs `pytest`.
- Add `SLIP_ENCRYPTION_KEY` to GitHub Secrets if your tests rely on it in CI.

6) Notes & troubleshooting

- Ensure secrets are never stored in the repository. Use Render / GitHub Secrets.
- If `REDIS_URL` is not provided, the app falls back to a localhost URL for dev. In production always provide a managed Redis.
- If a health endpoint is required for deployment health checks, add a simple Flask or FastAPI `/health` route that returns 200.
