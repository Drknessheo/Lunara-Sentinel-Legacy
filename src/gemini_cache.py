import json
import logging
import os

import redis

import config

logger = logging.getLogger(__name__)

# Connect to Redis using config/ENV
redis_url = os.environ.get("REDIS_URL") or getattr(config, "REDIS_URL", None)
if not redis_url:
    raise RuntimeError("REDIS_URL not configured for gemini_cache")
_r = redis.from_url(redis_url)


def _make_key(prefix: str, items) -> str:
    if isinstance(items, (list, tuple)):
        s = ",".join(items)
    else:
        s = str(items)
    return f"gemini:{prefix}:{s}"


def get_suggestions_for(symbols, ttl=300):
    """Return cached suggestions dict or None. Logs hits/misses and TTL when possible."""
    key = _make_key("suggestions", symbols)
    try:
        v = _r.get(key)
    except Exception as e:
        logger.debug(f"[GEMINI_CACHE] Redis get failed for {key}: {e}")
        return None

    if not v:
        logger.debug(f"[GEMINI_CACHE] MISS key={key}")
        return None

    # Attempt to read TTL for debugging
    try:
        ttl_val = _r.ttl(key)
    except Exception:
        ttl_val = None

    logger.debug(f"[GEMINI_CACHE] HIT key={key} ttl={ttl_val}")
    try:
        if isinstance(v, bytes):
            txt = v.decode("utf-8")
        else:
            txt = v
        return json.loads(txt)
    except Exception as e:
        logger.debug(f"[GEMINI_CACHE] Failed to parse cached JSON for {key}: {e}")
        return None


def set_suggestions_for(symbols, suggestions: dict, ttl=300):
    key = _make_key("suggestions", symbols)
    try:
        _r.setex(key, ttl, json.dumps(suggestions))
        logger.debug(
            f"[GEMINI_CACHE] Set key={key} ttl={ttl} size={len(json.dumps(suggestions))}"
        )
    except Exception as e:
        # best-effort cache
        logger.debug(f"[GEMINI_CACHE] Failed to set cache for {key}: {e}")

    # Also store metadata with timestamp so we can compute cache age
    try:
        meta_key = _make_key("meta:suggestions", symbols)
        meta = json.dumps({"ts": int(__import__("time").time()), "ttl": int(ttl)})
        _r.setex(meta_key, ttl + 60, meta)
        logger.debug(f"[GEMINI_CACHE] Set meta key={meta_key}")
    except Exception as e:
        logger.debug(f"[GEMINI_CACHE] Failed to set meta key for {key}: {e}")


def get_cache_age(symbols):
    """Return cache age in seconds (int) or None if not cached or unknown."""
    key = _make_key("meta:suggestions", symbols)
    try:
        v = _r.get(key)
    except Exception as e:
        logger.debug(f"[GEMINI_CACHE] Error reading meta key {key}: {e}")
        return None

    if not v:
        return None

    try:
        if isinstance(v, bytes):
            txt = v.decode("utf-8")
        else:
            txt = v
        meta = json.loads(txt)
        ts = int(meta.get("ts"))
        import time

        age = int(time.time()) - ts
        return age
    except Exception as e:
        logger.debug(f"[GEMINI_CACHE] Failed to parse meta for {key}: {e}")
        return None
