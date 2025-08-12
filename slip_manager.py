
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

# Connect to Redis
# Check if REDIS_URL is set in config, otherwise default to localhost
redis_url = config.REDIS_URL if hasattr(config, 'REDIS_URL') and config.REDIS_URL else 'redis://localhost:6379/0'
redis_client = redis.from_url(redis_url)

# Load the encryption key
key = config.BINANCE_ENCRYPTION_KEY
fernet = Fernet(key)

def create_and_store_slip(symbol, side, amount, price):
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
    redis_client.set(f"trade:{encrypted_slip.decode()}", encrypted_slip)
    return encrypted_slip

def get_and_decrypt_slip(encrypted_slip_key):
    logger.info(f"Attempting to decrypt slip: {encrypted_slip_key}")
    encrypted_slip_value = redis_client.get(encrypted_slip_key)
    if not encrypted_slip_value:
        logger.warning(f"No value found in Redis for slip key: {encrypted_slip_key}")
        return None
    try:
        decrypted_slip = fernet.decrypt(encrypted_slip_value)
        return json.loads(decrypted_slip.decode())
    except Exception as e:
        logger.error(f"Decryption failed for slip {encrypted_slip_key}: {e}")
        return None

def delete_slip(encrypted_slip_key):
    logger.info(f"Deleting slip: {encrypted_slip_key}")
    redis_client.delete(encrypted_slip_key)

def list_all_slips():
    """Lists all trade slips currently stored in Redis."""
    slips = []
    # Only retrieve keys that start with 'trade:'
    for key in redis_client.scan_iter("trade:*"):
        slip_data = get_and_decrypt_slip(key)
        if slip_data:
            slips.append({"key": key.decode(), "data": slip_data})
    return slips

def cleanup_slip(slip_key):
    """Deletes a specific slip from Redis by its key."""
    delete_slip(slip_key)

def clear_all_slips():
    """Delete all trade slip keys from Redis."""
    for key in redis_client.scan_iter("trade:*"):
        redis_client.delete(key)
