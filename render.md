# Deploying to Render.com

Required environment variables (set in the Render service):

- TELEGRAM_BOT_TOKEN: Your Telegram bot token
- ADMIN_USER_ID: Your admin user id (integer)
- REDIS_URL: redis URL (use a managed Redis in production)
- SLIP_ENCRYPTION_KEY: base64 Fernet key for slip encryption
- WEBHOOK_HMAC_SECRET: secret for incoming webhook verification

 Service setup recommendations:

 - Create a Web Service for the bot with the start command `python -m src.main`. The Render build will use the repo `Dockerfile` if present; ensure `WEBHOOK_HMAC_SECRET` and `SLIP_ENCRYPTION_KEY` are set in the service environment.
- If you need Redis, use Render's managed Redis addon and point `REDIS_URL` to it.
- Configure health checks to hit `/health` on the app (if you add an HTTP health endpoint).

Security notes:

- Store secrets in Render's environment variables (do not check them into source).
- Use `WEBHOOK_HMAC_SECRET` to verify incoming webhooks.
