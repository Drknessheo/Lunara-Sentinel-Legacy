import json
import os
import sys

import redis


def run_predeploy_tasks():
    """
    Safe, non-blocking pre-deploy tasks.

    Behavior changes from original:
    - Uses short Redis socket timeouts so the script won't hang.
    - Treats Redis unavailability as non-fatal: prints a warning and exits successfully
      so cloud deploys do not fail or hang.
    - Skips optional steps when configuration files are missing.
    """
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        print("Warning: REDIS_URL not set; skipping Redis pre-deploy tasks.")
        return

    print("Attempting to connect to Redis (short timeout)...")
    try:
        # short timeouts prevent long blocking during deployment
        r = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        r.ping()
        print("Redis connection successful.")
    except Exception as e:
        # Do not fail the deployment on Redis errors; just warn and continue.
        print(f"Warning: could not connect to Redis: {e}")
        print("Skipping Redis initialization steps.")
        return

    # 1. Clear a small set of keys (best-effort)
    try:
        keys_to_clear = ["slip_queue", "user_temp_flags"]
        print(f"Clearing Redis keys (best-effort): {keys_to_clear}")
        for key in keys_to_clear:
            try:
                r.delete(key)
            except Exception:
                pass
    except Exception:
        print("Warning: error while clearing Redis keys; continuing.")

    # 2. Load trading pairs configuration into Redis if present
    config_path = "config/trading_pairs.json"
    print(f"Loading trading pairs from {config_path} if available...")
    try:
        if os.path.exists(config_path):
            with open(config_path) as f:
                pairs_data = json.load(f)
                active_pairs = pairs_data.get("pairs", [])
                try:
                    r.set("active_pairs", json.dumps(active_pairs))
                    print(f"Loaded {len(active_pairs)} trading pairs into Redis.")
                except Exception:
                    print("Warning: failed to write trading pairs to Redis.")
        else:
            print(f"No trading pairs file at {config_path}; skipping.")
    except json.JSONDecodeError:
        print(f"Warning: could not decode JSON from {config_path}; skipping.")

    print("Pre-deploy tasks completed (best-effort).")


if __name__ == "__main__":
    try:
        run_predeploy_tasks()
    except Exception as e:
        # Never return a non-zero exit code from predeploy: keep deploys resilient
        print(f"Predeploy encountered an unexpected error: {e}")
    sys.exit(0)
