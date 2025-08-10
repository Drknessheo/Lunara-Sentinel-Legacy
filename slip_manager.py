
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
    redis_client.set(encrypted_slip, encrypted_slip, ex=300)  # TTL of 5 minutes
    return encrypted_slip

def get_and_decrypt_slip(encrypted_slip):
    print(f"Attempting to decrypt: {encrypted_slip}")
    # Retrieve the encrypted value from Redis using the key
    encrypted_slip_value = redis_client.get(encrypted_slip)
    if not encrypted_slip_value:
        print("No value found in Redis for this key.")
        return None
    try:
        decrypted_slip = fernet.decrypt(encrypted_slip_value)
        return json.loads(decrypted_slip.decode())
    except Exception as e:
        print(f"Decryption failed: {e}")
        return None
def clear_all_slips():
    """Delete all slip keys from Redis."""
    for key in redis_client.keys('*'):
        redis_client.delete(key)

def delete_slip(encrypted_slip):
    redis_client.delete(encrypted_slip)
