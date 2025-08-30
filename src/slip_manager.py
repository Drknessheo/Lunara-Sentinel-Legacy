# Fallback cache for slips if Redis is unavailable
fallback_cache = {}

import redis
from cryptography.fernet import Fernet
import json
import config
from datetime import datetime
import logging

logger = logging.getLogger("slip_manager")
logger.setLevel(logging.INFO)
if not logger.hasHandlers():
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

import os

# Connect to Redis
redis_url = os.getenv("REDIS_URL", 'redis://localhost:6379/0')

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
    redis_client = redis.from_url(redis_url, ssl_cert_reqs='none')
except Exception as e:
    logger.error(f"Failed to connect to Redis at {masked_url}. Error: {e}")
    raise ConnectionError(f"Failed to connect to Redis at {masked_url}.") from e

import functools

@functools.lru_cache()
def get_fernet():
    """Creates and caches the Fernet instance to avoid re-reading config."""
    key = config.BINANCE_ENCRYPTION_KEY
    if not key:
        raise ValueError("BINANCE_ENCRYPTION_KEY is not configured. Cannot proceed with encryption/decryption.")
    return Fernet(key)

def create_and_store_slip(symbol, side, amount, price):
    fernet = get_fernet()
    slip = {
        "symbol": symbol,
        "side": side,
        "amount": amount,
        "price": price,
        "timestamp": json.dumps(datetime.now(), indent=4, sort_keys=True, default=str)
    }
    json_slip = json.dumps(slip)
    encrypted_slip = fernet.encrypt(json_slip.encode())
    # Store with a prefix to easily identify trade slips
    try:
        redis_client.set(f"trade:{encrypted_slip.decode()}", encrypted_slip)
    except Exception as e:
        logger.error(f"Redis failed, storing slip in fallback cache: {e}")
        fallback_cache[f"trade:{encrypted_slip.decode()}"] = encrypted_slip
    return encrypted_slip

def get_and_decrypt_slip(encrypted_slip_key):
    fernet = get_fernet()
    logger.info(f"Attempting to decrypt slip: {encrypted_slip_key}")
    try:
        encrypted_slip_value = redis_client.get(encrypted_slip_key)
    except Exception:
        encrypted_slip_value = fallback_cache.get(encrypted_slip_key, None)
    if not encrypted_slip_value:
        logger.warning(f"No value found in Redis or fallback cache for slip key: {encrypted_slip_key}")
        return None
    try:
        decrypted_slip = fernet.decrypt(encrypted_slip_value)
        return json.loads(decrypted_slip.decode())
    except Exception as e:
        logger.error(f"Decryption failed for slip {encrypted_slip_key}: {e}")
        return None

def delete_slip(encrypted_slip_key):
    logger.info(f"Deleting slip: {encrypted_slip_key}")
    try:
        redis_client.delete(encrypted_slip_key)
    except Exception:
        fallback_cache.pop(encrypted_slip_key, None)

def list_all_slips():
    """Lists all trade slips currently stored in Redis."""
    slips = []
    try:
        # Only retrieve keys that start with 'trade:'
        for key in redis_client.scan_iter("trade:*"):
            slip_data = get_and_decrypt_slip(key)
            if slip_data:
                slips.append({"key": key.decode(), "data": slip_data})
    except Exception:
        # Fallback: list from local cache
        for key, encrypted_slip in fallback_cache.items():
            slip_data = None
            try:
                slip_data = get_and_decrypt_slip(key)
            except Exception:
                pass
            if slip_data:
                slips.append({"key": key, "data": slip_data})
    return slips

def cleanup_slip(slip_key):
    """Deletes a specific slip from Redis by its key."""
    delete_slip(slip_key)

def clear_all_slips():
    """Delete all trade slip keys from Redis."""
    for key in redis_client.scan_iter("trade:*"):
        redis_client.delete(key)
