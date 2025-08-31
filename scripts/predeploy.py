import json
import os

import redis


def run_predeploy_tasks():
    """
    Runs pre-deployment tasks to initialize the application environment.
    - Connects to Redis.
    - Clears specified Redis keys for a clean state.
    - Loads trading pairs from a JSON config file into Redis.
    """
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        print("Error: REDIS_URL environment variable not set.")
        print("Please set it in your .env file or environment.")
        return

    print("Connecting to Redis via URL...")
    try:
        r = redis.Redis.from_url(redis_url, decode_responses=True)
        r.ping()  # Check the connection
        print("Redis connection successful.")
    except redis.exceptions.ConnectionError as e:
        print(f"Error connecting to Redis: {e}")
        print("Please ensure your REDIS_URL is correct and the service is accessible.")
        return

    # 1. Clear outdated Redis keys
    keys_to_clear = ["slip_queue", "user_temp_flags"]
    print(f"Clearing Redis keys: {keys_to_clear}")
    for key in keys_to_clear:
        r.delete(key)

    # 2. Load trading pairs configuration into Redis
    config_path = "config/trading_pairs.json"
    print(f"Loading trading pairs from {config_path}...")
    try:
        with open(config_path) as f:
            pairs_data = json.load(f)
            # Assuming the JSON structure is {"pairs": [...]}
            active_pairs = pairs_data.get("pairs", [])
            r.set("active_pairs", json.dumps(active_pairs))
            print(f"Loaded {len(active_pairs)} trading pairs into Redis.")
    except FileNotFoundError:
        print(f"Warning: {config_path} not found. Skipping config load.")
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from {config_path}.")

    # --- Optional Firebase Integration Placeholder ---
    # If you use Firebase, you could add logic here to sync user data.
    # Example:
    # print("Syncing user subscriptions from Firebase...")
    # from firebase_admin import credentials, firestore, initialize_app
    # cred = credentials.Certificate("path/to/your/firebase-credentials.json")
    # initialize_app(cred)
    # db = firestore.client()
    # users_ref = db.collection('users')
    # for doc in users_ref.stream():
    #     user_id = doc.id
    #     membership = doc.to_dict().get('membership', 'Free')
    #     r.set(f"user:{user_id}:membership", membership)
    # print("Firebase sync complete.")

    print("\nPre-deploy tasks completed successfully.")


if __name__ == "__main__":
    run_predeploy_tasks()
