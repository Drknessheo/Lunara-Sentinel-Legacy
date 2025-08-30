# Lunara Bot — Quick Deployment Checklist

Run these locally before deploying to staging/production.

1) Environment
 - Ensure `.env` or environment variables include: REDIS_URL, TELEGRAM_BOT_TOKEN, ADMIN_USER_ID, SLIP_ENCRYPTION_KEY (if used)

2) Install deps
 - Create a virtualenv and install requirements.txt

3) Smoke test (local)
 - From repo root (PowerShell):
 ```powershell
 python .\scripts\deploy_smoke_test.py
 ```
 - Confirm: imports OK, DB init OK, Redis ping OK, autosuggest_audit peek visible

4) Telegram dry-run
 - Start the bot locally and invoke `/audit_recent 5` from admin Telegram account.
 - Verify formatting and sample entries.

5) If everything looks good
 - Deploy to staging; verify the same checks there.
 - Optionally enable autotrade monitor only in PAPER mode first.

Notes
 - `src/Lunessa_db.py` is a safe scaffold; the original uncleaned version is preserved as `src/Lunessa_db.py.bak`.
 - If Redis uses upstash (rediss://) make sure the environment allows outbound TLS connections.
