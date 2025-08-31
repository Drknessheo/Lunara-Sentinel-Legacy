import os

import redis
from dotenv import load_dotenv

# Load .env file from the project root
dotenv_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(dotenv_path=dotenv_path)

redis_url = os.getenv("REDIS_URL")

if not redis_url:
    print("Error: REDIS_URL not found in environment.")
    print(f"Please ensure it is set in {dotenv_path}")
else:
    print(f"Connecting to Redis at {redis_url}...")
    try:
        # Use from_url to connect
        r = redis.Redis.from_url(redis_url, decode_responses=True)

        # Test connection and operations
        r.ping()
        print("Connection successful.")

        r.set("lunessasignals:status", "connected and running")
        print("Set 'lunessasignals:status' to 'connected and running'")

        status = r.get("lunessasignals:status")
        print(f"Retrieved status: {status}")

    except redis.exceptions.ConnectionError as e:
        print(f"Failed to connect to Redis: {e}")
