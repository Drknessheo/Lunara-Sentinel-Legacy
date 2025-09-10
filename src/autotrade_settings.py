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


# --- Validation and setting API -------------------------------------------------
# Define canonical keys, defaults and ranges for safe validation
KEY_DEFINITIONS = {
    "rsi_buy": {
        "name": "RSI Buy Threshold",
        "default": 30.0,
        "type": float,
        "min": 15.0,
        "max": 40.0,
    },
    "rsi_sell": {
        "name": "RSI Sell Threshold",
        "default": 70.0,
        "type": float,
        "min": 60.0,
        "max": 85.0,
    },
    "stop_loss": {
        "name": "Stop Loss (%)",
        "default": 5.0,
        "type": float,
        "min": 1.0,
        "max": 10.0,
    },
    "trailing_activation": {
        "name": "Trailing Activation (%)",
        "default": 5.0,
        "type": float,
        "min": 2.0,
        "max": 15.0,
    },
    "trailing_drop": {
        "name": "Trailing Drop (%)",
        "default": 2.0,
        "type": float,
        "min": 0.1,
        "max": 5.0,
    },
    "bb_width": {
        "name": "Bollinger Band Width",
        "default": 2.0,
        "type": float,
        "min": 1.0,
        "max": 3.0,
    },
    "macd_signal": {
        "name": "MACD Signal Threshold",
        "default": 0.0,
        "type": float,
        "min": 0.0,
        "max": 100.0,
    },
    "trade_size": {
        "name": "Trade Size (USDT)",
        "default": 5.0,
        "type": float,
        "min": 5.0,
        "max": 1000000.0,
    },
}


def _coerce_value(raw: str, expected_type):
    if expected_type is float:
        try:
            return float(raw)
        except Exception:
            raise ValueError("must be a number")
    if expected_type is int:
        try:
            return int(raw)
        except Exception:
            raise ValueError("must be an integer")
    return raw


def validate_setting(key: str, raw_value: str):
    """Validate and coerce a single setting value. Returns (coerced_value, message).

    Raises ValueError on invalid key or invalid value.
    """
    key = key.strip()
    if key not in KEY_DEFINITIONS:
        raise ValueError("unknown setting")

    spec = KEY_DEFINITIONS[key]
    val = _coerce_value(raw_value, spec["type"])
    # Range check
    if "min" in spec and val < spec["min"]:
        raise ValueError(f"value too small; min={spec['min']}")
    if "max" in spec and val > spec["max"]:
        raise ValueError(f"value too large; max={spec['max']}")

    return val


def validate_and_set(user_id: int, key: str, raw_value: str, admin_scope: bool = False):
    """Validate and persist a setting for a user.

    Returns a tuple (success: bool, message: str).
    """
    key = key.strip()
    if key not in KEY_DEFINITIONS:
        return False, "Unknown setting key. Use /settings to view available keys."

    # allow 'reset' keyword to restore default
    if isinstance(raw_value, str) and raw_value.lower() == "reset":
        return reset_setting(user_id, key)

    try:
        coerced = validate_setting(key, str(raw_value))
    except ValueError as e:
        return False, f"Validation failed: {e}"

    # Inter-field sanity: trailing_drop < trailing_activation
    # Fetch stored settings, apply new value in-memory, and check
    stored = get_user_settings(user_id) or {}
    new = stored.copy()
    new[key] = coerced
    try:
        ta = float(
            new.get(
                "trailing_activation", KEY_DEFINITIONS["trailing_activation"]["default"]
            )
        )
        td = float(
            new.get("trailing_drop", KEY_DEFINITIONS["trailing_drop"]["default"])
        )
        if td >= ta:
            return (
                False,
                "Invalid setting: trailing_drop must be less than trailing_activation.",
            )
    except Exception:
        # ignore conversion errors here; individual validation already ran
        pass

    # Persist using Redis HSET for atomicity per-user
    client = get_redis()
    if not client:
        return False, "Storage unavailable (Redis)."

    redis_key = f"autotrade:settings:{user_id}"
    try:
        # store as JSON blob for simplicity (backwards compatible with existing set/get)
        stored_update = get_user_settings(user_id) or {}
        stored_update[key] = coerced
        client.set(redis_key, json.dumps(stored_update))
    except Exception as e:
        return False, f"Failed to save setting: {e}"

    return True, f"{KEY_DEFINITIONS[key]['name']} set to {coerced}."


def reset_setting(user_id: int, key: str):
    if key not in KEY_DEFINITIONS:
        return False, "Unknown setting key."
    client = get_redis()
    if not client:
        return False, "Storage unavailable (Redis)."
    redis_key = f"autotrade:settings:{user_id}"
    stored = get_user_settings(user_id) or {}
    if key in stored:
        try:
            del stored[key]
            client.set(redis_key, json.dumps(stored))
        except Exception as e:
            return False, f"Failed to reset setting: {e}"
    return (
        True,
        f"{KEY_DEFINITIONS[key]['name']} reset to default {KEY_DEFINITIONS[key]['default']}.",
    )


def export_settings_csv(user_id: int):
    """Return CSV string of current effective settings for user_id."""
    eff = get_effective_settings(user_id)
    # Prepare rows: key,label,value,default,min,max
    rows = ["key,label,value,default,min,max"]
    for k, spec in KEY_DEFINITIONS.items():
        val = eff.get(k, spec["default"])
        rows.append(
            f"{k},{spec['name']},{val},{spec['default']},{spec.get('min','')},{spec.get('max','')}"
        )
    return "\n".join(rows)
