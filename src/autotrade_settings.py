import os
import json
import sys
import redis
import config

# Connect to Redis using config/ENV
redis_url = os.environ.get('REDIS_URL') or getattr(config, 'REDIS_URL', None)
if not redis_url:
    raise RuntimeError('REDIS_URL not configured')
r = redis.from_url(redis_url)


def set_user_settings(user_id: int, settings: dict):
    key = f"autotrade:settings:{user_id}"
    r.set(key, json.dumps(settings))


def get_user_settings(user_id: int):
    key = f"autotrade:settings:{user_id}"
    v = r.get(key)
    if not v:
        return None
    try:
        return json.loads(v.decode('utf-8'))
    except Exception:
        return None


def get_effective_settings(user_id: int):
    # Merge defaults from config with stored overrides
    defaults = {
        'target_price': None,
        'max_hold_time': 3600,
        'signal': None,
        'PROFIT_TARGET_PERCENTAGE': 1.0,
        'STOP_LOSS_PERCENTAGE': 5.0,
        'TRADE_SIZE_USDT': 5.0
    }
    stored = get_user_settings(user_id) or {}
    merged = defaults.copy()
    merged.update(stored)
    return merged
