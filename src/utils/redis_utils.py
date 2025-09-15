"""
This module provides utility functions for interacting with Redis.
"""

import redis
import os

def clear_redis_cache():
    """Clears the Redis cache."""
    try:
        redis_url = os.getenv("REDIS_URL")
        if not redis_url:
            return False, "REDIS_URL not configured."

        r = redis.from_url(redis_url)
        r.flushall()
        return True, "Redis cache cleared successfully."
    except Exception as e:
        return False, f"An error occurred while clearing the Redis cache: {e}"
