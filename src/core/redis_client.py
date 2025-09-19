import redis.asyncio as redis
from functools import lru_cache
import logging
from .. import config

logger = logging.getLogger(__name__)

@lru_cache(maxsize=1)
def get_redis_client():
    """
    Returns a Redis client instance, cached for performance.
    Connects using the URL from the application's configuration.
    """
    redis_url = getattr(config, 'REDIS_URL', None)
    if not redis_url:
        logger.critical("CRITICAL: REDIS_URL is not configured!")
        raise ValueError("REDIS_URL is not set in the configuration.")
    
    try:
        client = redis.from_url(redis_url, decode_responses=True)
        logger.info("Successfully created and cached Redis client.")
        return client
    except Exception as e:
        logger.critical(f"CRITICAL: Failed to connect to Redis at {redis_url}. Error: {e}")
        raise
