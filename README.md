# 🌌 Lunara Bot

**Lunara Bot** is a modular, intelligent crypto trading assistant powered by Telegram Bot API and Python. Designed for real-time signal alerts, trade tracking, and strategy guidance — Lunara is your trusted sidekick on the path to growing $10 into $1000 through disciplined swing trading.

---

## 🚀 Features

- 📲 **Telegram Bot Interface** — Trade insights delivered to your chat
- 💹 **Crypto Trade Tracking** — Keep records of entry, exit, PnL
- 🧠 **Strategy Logic** — Includes RSI, signal mirroring, and gain planning
- 🗃️ **SQLite Database** — Lightweight local trade history management
- 🛎️ **Telegram Signal Scanner** — (Planned) Real-time signal scraping
- 🧭 **Spiritual Gamification Layer** *(optional)* — Track gains as "Resonance" points

---

## 🛠️ Installation

> 📱 Best used on **Android (via Termux)** or **Linux systems**

### 1. Clone the repo

```bash
git clone https://github.com/Drknessheo/lunara-bot.git
cd lunara-bot
```

### 2. Setup virtual environment

```bash
pkg install python git
pip install virtualenv
virtualenv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Add your bot token

Edit `main.py` and replace:

```python
TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
```

Or store it as an environment variable for safety.

---

## 🧪 Running the Bot

```bash
python main.py
```

The bot will log startup in the console and begin listening for messages.

---

## 📂 Folder Structure

```
lunara-bot/
├── main.py               # Entry point for the bot
├── db.py                 # SQLite DB setup & interaction
├── strategy.py           # Core trading logic
├── scheduler.py          # Task scheduler
├── utils.py              # Helper functions
├── requirements.txt      # Python dependencies
└── README.md             # Project documentation
```

---

## ⚖️ License

This project is licensed under the **MIT License** — see [`LICENSE`](LICENSE) for details.

You are free to:

* Use
* Modify
* Share
* Even commercialize

Just credit the original author.

---

## 🙌 Author

**Shamim Reza Saikat**
Telegram: [@Drknessheo](https://t.me/drknessheo)
Email: [s_r_saikat@yahoo.com](mailto:s_r_saikat@yahoo.com)

Follow the journey of Lunara across the cosmos of logic, spirit, and crypto mastery.

---

## 🌠 Vision
Lunara is your AI-powered crypto trading companion, harmonizing intention, signal, and market flow for disciplined, secure, and scalable trading.

---

## Webhook Retry System

Failed promotion webhooks are automatically enqueued and retried with exponential backoff. This helps ensure promotions are delivered reliably even when receivers are temporarily unavailable.

Admin commands:
- `/retry_queue` — list pending retries
- `/retry_dispatch <index>` — manually retry one
- `/retry_flush confirm` — clear the queue
- `/retry_stats` — show retry metrics

Redis keys used:
- `promotion_webhook_retry` — pending items
- `promotion_webhook_failed` — permanently failed
- `promotion_log` — successful dispatches

Usage:
Send `/retry_stats` in any admin-approved thread or DM to get a quick pulse on retry health.

### Redis Metrics (promotion_webhook_stats)

Stored in Redis hash `promotion_webhook_stats`:

- `pending`: Number of items currently in the retry queue
- `failed`: Total number of failed dispatches moved to failed list
- `total_sent`: Total successful dispatches (via retry)
- `last_failed_ts`: ISO timestamp of the most recent failure

View manually:
```bash
redis-cli HGETALL promotion_webhook_stats
```

Or use `/retry_stats` to view in bot output.

Join us in this fusion of trading and metaphysical clarity.

---

## Redis URL handling and TLS (REDIS_USE_TLS)

This project centralizes Redis client creation via `src.redis_utils.get_redis_client(...)`.
To make the bot compatible with providers that return scheme-less URLs (for example Upstash)
and to avoid leaking credentials in logs, the helper normalizes and masks Redis URLs.

Key behaviors:
- If your environment contains `REDIS_USE_TLS=true` (case-insensitive), the helper will prefer `rediss://` (TLS) when constructing a Redis URL.
- If `REDIS_USE_TLS` is not set, the helper will automatically prefer `rediss://` for hosts that contain `upstash` (heuristic), and will otherwise use `redis://`.
- Scheme-less Upstash-style URLs that start with `//user:pass@host:port` are accepted and will be prefixed with the chosen scheme.
- All masked logging uses `mask_redis_url(...)` so credentials (user:pass) are replaced with `***:***` in logs.

Example env vars:

```bash
# Force TLS (recommended for production / Upstash endpoints)
export REDIS_USE_TLS=true

# Example Upstash-style URL (scheme-less) - helper will pick rediss:// when REDIS_USE_TLS is true
export REDIS_URL="//default:...@us1-upstash.redis.upstash.io:6379"
```

This small behavior ensures the bot accepts both local `redis://host.docker.internal:6379`
and cloud `rediss://...upstash.io` endpoints without changing code in many places.