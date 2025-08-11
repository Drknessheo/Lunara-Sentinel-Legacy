
import redis
from cryptography.fernet import Fernet
import json
import config

# Connect to Redis
redis_client = redis.Redis(host='localhost', port=6379, db=0)

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
    redis_client.set(encrypted_slip, encrypted_slip)
    return encrypted_slip

def get_and_decrypt_slip(encrypted_slip):
    import logging
    logger = logging.getLogger("slip_manager")
    logger.setLevel(logging.INFO)
    if not logger.hasHandlers():
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.info(f"Attempting to decrypt slip: {encrypted_slip}")
    encrypted_slip_value = redis_client.get(encrypted_slip)
    if not encrypted_slip_value:
        logger.warning(f"No value found in Redis for slip key: {encrypted_slip}")
        return {"error": "not_found", "key": encrypted_slip}
    try:
        decrypted_slip = fernet.decrypt(encrypted_slip_value)
        return json.loads(decrypted_slip.decode())
    except Exception as e:
        logger.error(f"Decryption failed for slip {encrypted_slip}: {e}")
        return {"error": "decryption_failed", "key": encrypted_slip, "exception": str(e)}
def clear_all_slips():
    """Delete all slip keys from Redis."""
    for key in redis_client.keys('*'):
        redis_client.delete(key)

def delete_slip(encrypted_slip):
    redis_client.delete(encrypted_slip)
