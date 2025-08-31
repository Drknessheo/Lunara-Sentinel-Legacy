# Fallback cache for slips if Redis is unavailable
fallback_cache = {}

import json
import logging
from datetime import datetime

import redis
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

# Connect to Redis
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Sanitize the Redis URL to remove any duplicate schemes
if redis_url.count("rediss://") > 1:
    redis_url = "rediss://" + redis_url.rsplit("rediss://", 1)[-1]
elif redis_url.count("redis://") > 1:
    redis_url = "redis://" + redis_url.rsplit("redis://", 1)[-1]

# Mask the Redis URL for logging
masked_url = redis_url
if "@" in masked_url:
    protocol, _, rest = redis_url.partition("://")
    _, _, host_part = rest.partition("@")
    masked_url = f"{protocol}://***:***@{host_part}"

logger.info(f"Connecting to Redis at {masked_url}")
try:
    redis_client = redis.from_url(redis_url, ssl_cert_reqs="none")
except Exception as e:
    logger.error(f"Failed to connect to Redis at {masked_url}. Error: {e}")
    raise ConnectionError(f"Failed to connect to Redis at {masked_url}.") from e

import functools


@functools.lru_cache()
def get_fernet():
    """Creates and caches the Fernet instance to avoid re-reading config.

    Behavior:
    - Prefer `config.SLIP_ENCRYPTION_KEY` if present (canonical for slip storage).
    - Fall back to `config.BINANCE_ENCRYPTION_KEY` for backwards compatibility.
    - Accept either bytes or str for the key value.
    """
    # Prefer explicit SLIP_ENCRYPTION_KEY, otherwise fall back to BINANCE_ENCRYPTION_KEY
    key = None
    if getattr(config, "SLIP_ENCRYPTION_KEY", None):
        key = config.SLIP_ENCRYPTION_KEY
    elif getattr(config, "BINANCE_ENCRYPTION_KEY", None):
        key = config.BINANCE_ENCRYPTION_KEY

    if not key:
        raise ValueError(
            "No encryption key configured. Set SLIP_ENCRYPTION_KEY or BINANCE_ENCRYPTION_KEY in env."
        )

    # Ensure the key is bytes
    if isinstance(key, str):
        key = key.encode()

    return Fernet(key)


def create_and_store_slip(symbol, side, amount, price):
    """
    Create and store a slip using per-field keys to make reconstruction and cleanup simple.
    Returns the trade_id used.
    """
    fernet = get_fernet()
    # Use a simple incremental trade id (timestamp-based) to avoid exposing ciphertext in keys
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

    # Store a per-trade full payload and per-field pieces for compatibility
    try:
        redis_client.set(f"trade:{trade_id}:data", encrypted_slip)
        redis_client.set(f"trade:{trade_id}:status", fernet.encrypt(b"open"))
        redis_client.set(
            f"trade:{trade_id}:quantity", fernet.encrypt(str(amount).encode())
        )
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
    logger.debug(f"Attempting to decrypt slip: {encrypted_slip_key}")
    try:
        encrypted_slip_value = redis_client.get(encrypted_slip_key)
    except Exception:
        encrypted_slip_value = fallback_cache.get(encrypted_slip_key, None)
    if not encrypted_slip_value:
        logger.warning(
            f"No value found in Redis or fallback cache for slip key: {encrypted_slip_key}"
        )
        return None
    try:
        decrypted_slip = fernet.decrypt(encrypted_slip_value)
        text = decrypted_slip.decode("utf-8", errors="ignore").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            # Try to interpret as a primitive (number) or return raw string
            try:
                return float(text)
            except Exception:
                return text
    except Exception as e:
        logger.error(f"Decryption failed for slip {encrypted_slip_key}: {e}")
        return None


def delete_slip(encrypted_slip_key):
    """Delete a slip key or all keys for a trade id.
    If passed a key like 'trade:<id>' or 'trade:<id>:field' or just the id, delete all related keys.
    """
    logger.info(f"Deleting slip: {encrypted_slip_key}")
    # Normalize to string trade id when possible
    try:
        k = (
            encrypted_slip_key.decode()
            if isinstance(encrypted_slip_key, (bytes, bytearray))
            else str(encrypted_slip_key)
        )
    except Exception:
        k = str(encrypted_slip_key)

    # If caller passed 'trade:<id>' or 'trade:<id>:field', extract id
    parts = k.split(":")
    if len(parts) >= 2 and parts[0] == "trade":
        trade_id = parts[1]
        # delete all keys starting with trade:<id>
        try:
            for rk in redis_client.scan_iter(f"trade:{trade_id}*"):
                redis_client.delete(rk)
        except Exception:
            # fallback cache cleanup
            keys_to_remove = [
                kk
                for kk in list(fallback_cache.keys())
                if kk.startswith(f"trade:{trade_id}")
            ]
            for kk in keys_to_remove:
                fallback_cache.pop(kk, None)
        return

    # Otherwise attempt to delete the exact key
    try:
        redis_client.delete(k)
    except Exception:
        fallback_cache.pop(k, None)


def list_all_slips():
    """Lists all trade slips currently stored in Redis."""
    slips = []
    try:
        raw_keys = list(redis_client.scan_iter("trade:*"))
        is_bytes = any(isinstance(k, (bytes, bytearray)) for k in raw_keys)
    except Exception:
        raw_keys = list(fallback_cache.keys())
        is_bytes = False

    # Normalize to string keys and group by trade id
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
        # Prefer a full slip stored at 'trade:<id>' if available
        full_key = f"trade:{trade_id}"
        slip_data = None
        if full_key in keys:
            # Pass the key in the same type as raw_keys contained
            key_to_use = full_key.encode() if is_bytes else full_key
            slip_data = get_and_decrypt_slip(key_to_use)
            if isinstance(slip_data, dict):
                slips.append({"key": full_key, "data": slip_data})
                continue

        # Otherwise, reconstruct from per-field keys
        fields = {}
        for kk in keys:
            if kk == full_key:
                continue
            # kk like 'trade:49:quantity' -> field is last part
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
            # Normalize common field names
            # Some storage uses 'quantity' vs 'amount'
            if "quantity" in fields and "amount" not in fields:
                fields["amount"] = fields["quantity"]
            slips.append({"key": full_key, "data": fields})

    return slips


def cleanup_slip(slip_key):
    """Deletes a specific slip from Redis by its key."""
    delete_slip(slip_key)


def clear_all_slips():
    """Delete all trade slip keys from Redis."""
    for key in redis_client.scan_iter("trade:*"):
        redis_client.delete(key)
