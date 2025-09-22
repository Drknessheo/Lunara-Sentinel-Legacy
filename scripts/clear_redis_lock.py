
import os
import redis
from dotenv import load_dotenv
import sys

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.redis_client import MASTER_LOCK_KEY

def clear_master_lock():
    """
    Connects to Redis and manually clears the master lock.
    """
    dotenv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(dotenv_path):
        print(f"[CONFIG] Loading environment from: {dotenv_path}")
        load_dotenv(dotenv_path=dotenv_path)
    else:
        print(f"[CONFIG] Warning: .env file not found at {dotenv_path}. Relying on system environment variables.")

    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        print("Error: REDIS_URL not found in your environment configuration.")
        return

    try:
        print(f"Connecting to Redis at {redis_url}...")
        redis_client = redis.from_url(redis_url)
        
        print(f"Attempting to delete lock key: '{MASTER_LOCK_KEY}'...")
        result = redis_client.delete(MASTER_LOCK_KEY)
        
        if result == 1:
            print("Successfully cleared the master lock.")
        else:
            print("No active master lock was found to clear.")
            
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    clear_master_lock()
