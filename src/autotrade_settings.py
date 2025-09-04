import json
import os
from typing import Optional

import config


# Lazy import of redis and client creation to avoid raising at import-time
def get_redis():
    """Return a redis client. In tests this can be monkeypatched to return
    a fakeredis instance. If REDIS_URL is not configured, return None.
    """
    try:
        import redis

        # Use environment URL first; if not present, fall back to config.REDIS_URL
        # If still not present, use a localhost default so tests that monkeypatch
        # redis.from_url receive a call and can return a fakeredis instance.
        redis_url = (
            os.environ.get("REDIS_URL")
            or getattr(config, "REDIS_URL", None)
            or "redis://localhost:6379/0"
        )
        return redis.from_url(redis_url)
    except Exception:
        return None


def set_user_settings(user_id: int, settings: dict):
    key = f"autotrade:settings:{user_id}"
    client = get_redis()
    if not client:
        # Best-effort: if Redis not available, silently no-op in tests/local
        return
    client.set(key, json.dumps(settings))


def get_user_settings(user_id: int) -> Optional[dict]:
    key = f"autotrade:settings:{user_id}"
    client = get_redis()
    if not client:
        return None
    v = client.get(key)
    if not v:
        return None
    try:
        # redis.from_url with decode_responses may already return str
        if isinstance(v, (bytes, bytearray)):
            return json.loads(v.decode("utf-8"))
        return json.loads(v)
    except Exception:
        return None


def get_effective_settings(user_id: int):
    # Merge defaults from config with stored overrides
    defaults = {
        "target_price": None,
        "max_hold_time": 3600,
        "signal": None,
        "PROFIT_TARGET_PERCENTAGE": getattr(config, "DEFAULT_SETTINGS", {}).get(
            "PROFIT_TARGET_PERCENTAGE", 1.0
        ),
        "STOP_LOSS_PERCENTAGE": 5.0,
        "TRADE_SIZE_USDT": 5.0,
    }
    stored = get_user_settings(user_id) or {}
    merged = defaults.copy()
    merged.update(stored)
    return merged
