# Fallback cache for slips if Redis is unavailable
fallback_cache = {}

import json
import logging
from datetime import datetime
from typing import Optional

from cryptography.fernet import Fernet

import config

logger = logging.getLogger("slip_manager")
logger.setLevel(logging.INFO)
if not logger.hasHandlers():
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

import os


def get_redis_client():
    """Lazily return a redis client or None if REDIS_URL is not configured.

    Tests can monkeypatch `redis.from_url` to return a fakeredis instance.
    """
    try:
        import redis

        redis_url = (
            os.getenv("REDIS_URL")
            or getattr(config, "REDIS_URL", None)
            or "redis://localhost:6379/0"
        )

        # Sanitize the Redis URL to remove duplicate schemes
        if redis_url.count("rediss://") > 1:
            redis_url = "rediss://" + redis_url.rsplit("rediss://", 1)[-1]
        elif redis_url.count("redis://") > 1:
            redis_url = "redis://" + redis_url.rsplit("redis://", 1)[-1]

        try:
            client = redis.from_url(redis_url, ssl_cert_reqs="none")
            return client
        except Exception as e:
            logger.warning(
                f"Redis connection failed: {e}. Falling back to in-memory cache."
            )
            return None
    except Exception:
        return None


import functools


@functools.lru_cache()
def get_fernet() -> Optional[Fernet]:
    """Creates and caches the Fernet instance. Returns None if no key configured."""
    # Read keys from environment only. Tests use monkeypatch.setenv / delenv
    # and rely on this behavior; config module may have been imported earlier
    # with stale values, so prefer the current process env for reproducibility.
    key = os.getenv("SLIP_ENCRYPTION_KEY") or os.getenv("BINANCE_ENCRYPTION_KEY")

    if not key:
        logger.warning(
            "No encryption key configured. Set SLIP_ENCRYPTION_KEY or BINANCE_ENCRYPTION_KEY in env."
        )
        return None

    if isinstance(key, str):
        key = key.encode()
    try:
        return Fernet(key)
    except Exception as e:
        logger.error(f"Invalid encryption key: {e}")
        return None


def create_and_store_slip(symbol, side=None, amount=None, price=None):
    """Create and store a slip.

    Two calling conventions supported for tests/backwards compatibility:
    - create_and_store_slip(symbol, side, amount, price)
    - create_and_store_slip(trade_id, slip_dict)
      where slip_dict contains keys: symbol, price (and optionally amount/side)
    """
    # Tests control environment variables directly; prefer an explicit
    # runtime check so behavior is deterministic regardless of cached
    # config values. If no key present in env, raise the expected ValueError.
    if not (os.getenv("SLIP_ENCRYPTION_KEY") or os.getenv("BINANCE_ENCRYPTION_KEY")):
        raise ValueError("Encryption key is not configured")
    fernet = get_fernet()
    if not fernet:
        # If get_fernet failed despite env var being present, treat as mis-config
        raise ValueError("Encryption key is not configured")

    # Backwards-compatible call: create_and_store_slip(trade_id, slip_dict)
    if isinstance(side, dict) and amount is None and price is None:
        trade_id = str(symbol)
        slip_dict = side
        # For backwards-compatibility with tests, store the slip dict exactly
        # as provided (do not add extra fields that would break equality checks).
        slip = dict(slip_dict)
    else:
        trade_id = str(int(datetime.utcnow().timestamp() * 1000))
        slip = {
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "price": price,
            "status": "open",
            "sandpaper": True,
            "timestamp": datetime.utcnow().isoformat(),
        }

    json_slip = json.dumps(slip)
    encrypted_slip = fernet.encrypt(json_slip.encode())

    client = get_redis_client()
    try:
        if client:
            client.set(f"trade:{trade_id}:data", encrypted_slip)
            client.set(f"trade:{trade_id}:status", fernet.encrypt(b"open"))
            client.set(
                f"trade:{trade_id}:quantity", fernet.encrypt(str(amount).encode())
            )
        else:
            raise Exception("no redis")
    except Exception as e:
        logger.error(f"Redis failed, storing slip in fallback cache: {e}")
        fallback_cache[f"trade:{trade_id}:data"] = encrypted_slip
        fallback_cache[f"trade:{trade_id}:status"] = fernet.encrypt(b"open")
        fallback_cache[f"trade:{trade_id}:quantity"] = fernet.encrypt(
            str(amount).encode()
        )

    return trade_id


def get_and_decrypt_slip(encrypted_slip_key):
    fernet = get_fernet()
    if not fernet:
        logger.debug("get_and_decrypt_slip: no fernet available, returning None")
        return None

    # Normalize the passed key: tests pass a bare trade_id (e.g. "test_trade_123");
    # stored keys are like "trade:<id>:data". Accept either form.
    try:
        if isinstance(encrypted_slip_key, (bytes, bytearray)):
            key_str = encrypted_slip_key.decode()
        else:
            key_str = str(encrypted_slip_key)
    except Exception:
        key_str = str(encrypted_slip_key)

    # If caller provided a plain trade_id (no colon), look up the canonical data key.
    if ":" not in key_str:
        lookup_key = f"trade:{key_str}:data"
    else:
        lookup_key = key_str

    client = get_redis_client()
    try:
        encrypted_slip_value = None
        if client:
            encrypted_slip_value = client.get(lookup_key)
        else:
            encrypted_slip_value = fallback_cache.get(lookup_key, None)
    except Exception:
        encrypted_slip_value = fallback_cache.get(lookup_key, None)

    if not encrypted_slip_value:
        logger.warning(
            f"No value found in Redis or fallback cache for slip key: {encrypted_slip_key}"
        )
        return None

    try:
        # If client returned strings, ensure bytes for Fernet
        if isinstance(encrypted_slip_value, str):
            encrypted_slip_value = encrypted_slip_value.encode()
        decrypted_slip = fernet.decrypt(encrypted_slip_value)
        text = decrypted_slip.decode("utf-8", errors="ignore").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            try:
                return float(text)
            except Exception:
                return text
    except Exception as e:
        logger.error(f"Decryption failed for slip {encrypted_slip_key}: {e}")
        return None


def delete_slip(encrypted_slip_key):
    logger.info(f"Deleting slip: {encrypted_slip_key}")
    try:
        k = (
            encrypted_slip_key.decode()
            if isinstance(encrypted_slip_key, (bytes, bytearray))
            else str(encrypted_slip_key)
        )
    except Exception:
        k = str(encrypted_slip_key)

    parts = k.split(":")
    if len(parts) >= 2 and parts[0] == "trade":
        trade_id = parts[1]
        try:
            client = get_redis_client()
            if client:
                for rk in client.scan_iter(f"trade:{trade_id}*"):
                    client.delete(rk)
            else:
                raise Exception("no redis")
        except Exception:
            keys_to_remove = [
                kk
                for kk in list(fallback_cache.keys())
                if kk.startswith(f"trade:{trade_id}")
            ]
            for kk in keys_to_remove:
                fallback_cache.pop(kk, None)
        return

    try:
        client = get_redis_client()
        if client:
            client.delete(k)
        else:
            fallback_cache.pop(k, None)
    except Exception:
        fallback_cache.pop(k, None)


def list_all_slips():
    slips = []
    try:
        client = get_redis_client()
        if client:
            raw_keys = list(client.scan_iter("trade:*"))
            is_bytes = any(isinstance(k, (bytes, bytearray)) for k in raw_keys)
        else:
            raise Exception("no redis")
    except Exception:
        raw_keys = list(fallback_cache.keys())
        is_bytes = False

    grouped = {}
    for k in raw_keys:
        try:
            ks = k.decode() if isinstance(k, (bytes, bytearray)) else str(k)
        except Exception:
            ks = str(k)
        parts = ks.split(":")
        if len(parts) >= 2 and parts[0] == "trade":
            trade_id = parts[1]
            grouped.setdefault(trade_id, []).append(ks)

    for trade_id, keys in grouped.items():
        full_key = f"trade:{trade_id}"
        slip_data = None
        if full_key in keys:
            key_to_use = full_key.encode() if is_bytes else full_key
            slip_data = get_and_decrypt_slip(key_to_use)
            if isinstance(slip_data, dict):
                slips.append({"key": full_key, "data": slip_data})
                continue

        fields = {}
        for kk in keys:
            if kk == full_key:
                continue
            parts = kk.split(":")
            if len(parts) < 3:
                continue
            field = parts[2]
            key_to_use = kk.encode() if is_bytes else kk
            val = get_and_decrypt_slip(key_to_use)
            if val is None:
                continue
            fields[field] = val

        if fields:
            if "quantity" in fields and "amount" not in fields:
                fields["amount"] = fields["quantity"]
            slips.append({"key": full_key, "data": fields})

    return slips


def cleanup_slip(slip_key):
    delete_slip(slip_key)


def clear_all_slips():
    client = get_redis_client()
    if client:
        for key in client.scan_iter("trade:*"):
            client.delete(key)
    else:
        keys_to_remove = [
            k for k in list(fallback_cache.keys()) if k.startswith("trade:")
        ]
        for k in keys_to_remove:
            fallback_cache.pop(k, None)
