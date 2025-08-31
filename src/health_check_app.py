import json
import logging
import os
import time

import redis
from flask import Flask, jsonify, redirect

app = Flask(__name__)

# uptime tracking
start_time = time.time()

# Disable Werkzeug's default logger for successful requests
log = logging.getLogger("werkzeug")
log.setLevel(logging.WARNING)  # Only log errors


@app.route("/")
def home():
    return "✅ Lunessa Bot is running. Join us on Telegram."


@app.route("/telegram")
def telegram():
    return redirect(
        "https://t.me/YOUR_CHANNEL_NAME"
    )  # Replace with your actual channel


@app.route("/healthz")
def healthz():
    # This endpoint is for Render's health check.
    # It returns a simple 200 OK without logging to keep logs clean.
    return "", 200


@app.route("/health")
def health():
    # Provide a lightweight JSON health response for platforms or probes that
    # expect JSON at /health. Keep it small to avoid large logs.
    try:
        r = redis.from_url(os.environ.get("REDIS_URL", ""), socket_timeout=1)
        redis_ok = True
        try:
            r.ping()
        except Exception:
            redis_ok = False
    except Exception:
        redis_ok = False

    return jsonify({"status": "ok", "redis": bool(redis_ok)}), 200


@app.route("/status")
def status():
    """Return combined trade info and health checks.

    - uptime_seconds
    - autotrade_enabled (for ADMIN_USER_ID)
    - last_trade_id and pending_trades (if present in Redis)
    """
    uptime = int(time.time() - start_time)

    # Safe Redis connection
    try:
        r = redis.from_url(os.environ.get("REDIS_URL", ""), socket_timeout=2)
    except Exception:
        r = None

    trade_info = {"last_trade_id": None, "pending_trades": 0}

    if r:
        try:
            last = r.get("trade:last_id")
            trade_info["last_trade_id"] = last.decode() if last else None
        except Exception:
            trade_info["last_trade_id"] = None

        try:
            pend = r.get("trade:pending_count")
            trade_info["pending_trades"] = int(pend) if pend else 0
        except Exception:
            trade_info["pending_trades"] = 0

    admin_id = os.environ.get("ADMIN_USER_ID")
    autotrade_enabled = False
    if r and admin_id:
        try:
            val = r.get(f"autotrade:{admin_id}")
            autotrade_enabled = val == b"True" or (
                isinstance(val, str) and val == "True"
            )
        except Exception:
            autotrade_enabled = False

    health = {
        "uptime_seconds": uptime,
        "autotrade_enabled": autotrade_enabled,
        "timestamp": int(time.time()),
    }

    # Additional operational metrics
    metrics = {
        "promotion_retry_pending": None,
        "promotion_failed": None,
        "promotion_log": None,
        "trade_issues": None,
        "promotion_webhook_stats": {},
        "recent_trade_issues": [],
    }

    if r:
        try:
            metrics["promotion_retry_pending"] = r.llen("promotion_webhook_retry")
        except Exception:
            metrics["promotion_retry_pending"] = None
        try:
            metrics["promotion_failed"] = r.llen("promotion_webhook_failed")
        except Exception:
            metrics["promotion_failed"] = None
        try:
            metrics["promotion_log"] = r.llen("promotion_log")
        except Exception:
            metrics["promotion_log"] = None
        try:
            metrics["trade_issues"] = r.llen("trade_issues")
        except Exception:
            metrics["trade_issues"] = None

        # Read promotion_webhook_stats hash if present
        try:
            stats = r.hgetall("promotion_webhook_stats") or {}
            # Convert numeric strings to ints where possible
            parsed = {}
            for k, v in stats.items():
                try:
                    parsed[k] = int(v)
                except Exception:
                    parsed[k] = v
            metrics["promotion_webhook_stats"] = parsed
        except Exception:
            metrics["promotion_webhook_stats"] = {}

        # Provide a short list of recent trade issues for quick diagnostics
        try:
            raw_issues = r.lrange("trade_issues", 0, 4) or []
            for it in raw_issues:
                try:
                    obj = json.loads(it)
                    metrics["recent_trade_issues"].append(
                        {
                            "trade_id": obj.get("trade_id"),
                            "user_id": obj.get("user_id"),
                            "symbol": obj.get("symbol"),
                            "quantity": obj.get("quantity"),
                            "ts": obj.get("ts") or obj.get("timestamp"),
                        }
                    )
                except Exception:
                    metrics["recent_trade_issues"].append({"raw": str(it)[:200]})
        except Exception:
            metrics["recent_trade_issues"] = []

    return jsonify({"trade_info": trade_info, "health": health, "metrics": metrics})


if __name__ == "__main__":
    # Bind to 0.0.0.0 so the container is reachable from the outside network.
    port = int(os.environ.get("PORT", 8080))
    # threaded=True keeps the app responsive while the bot runs in the background
    app.run(host="0.0.0.0", port=port, threaded=True)
