
import redis
import logging
from . import config

logger = logging.getLogger(__name__)

redis_client = None

try:
    # Use fakeredis for local development/testing if specified
    if config.USE_FAKE_REDIS:
        import fakeredis
        redis_client = fakeredis.FakeRedis(decode_responses=True)
        logger.info("Using FakeRedis for in-memory session management.")
    else:
        # Connect to a real Redis server in production
        redis_client = redis.from_url(config.REDIS_URL, decode_responses=True)
        # Test the connection
        redis_client.ping()
        logger.info("Successfully connected to Redis server.")

except redis.exceptions.ConnectionError as e:
    logger.critical(f"Could not connect to Redis server at {config.REDIS_URL}. Please check the connection.")
    logger.critical("Redis is essential for the bot's real-time state management. The bot cannot run without it.")
    # In a real-world scenario, you might want to exit the application here
    # For now, we will log a critical error and let the app fail on its own.
    redis_client = None 
except Exception as e:
    logger.critical(f"An unexpected error occurred while initializing Redis: {e}", exc_info=True)
    redis_client = None

def get_redis_client():
    """Returns the configured Redis client instance."""
    if redis_client is None:
        raise ConnectionError("Redis client is not available. The application cannot proceed.")
    return redis_client

def get_active_trades_key(user_id: int) -> str:
    """Returns the Redis key for the set of a user's active trades."""
    return f"user:{user_id}:active_trades"

def add_active_trade(user_id: int, symbol: str):
    """Adds a symbol to the user's set of active trades in Redis."""
    try:
        client = get_redis_client()
        key = get_active_trades_key(user_id)
        client.sadd(key, symbol)
        logger.info(f"[REDIS] Added {symbol} to active trades for user {user_id}.")
    except Exception as e:
        logger.error(f"[REDIS] Failed to add active trade for user {user_id}: {e}")

def remove_active_trade(user_id: int, symbol: str):
    """Removes a symbol from the user's set of active trades in Redis."""
    try:
        client = get_redis_client()
        key = get_active_trades_key(user_id)
        client.srem(key, symbol)
        logger.info(f"[REDIS] Removed {symbol} from active trades for user {user_id}.")
    except Exception as e:
        logger.error(f"[REDIS] Failed to remove active trade for user {user_id}: {e}")

def get_active_trades(user_id: int) -> set:
    """Gets the set of active trade symbols for a user from Redis."""
    try:
        client = get_redis_client()
        key = get_active_trades_key(user_id)
        return client.smembers(key) or set()
    except Exception as e:
        logger.error(f"[REDIS] Failed to get active trades for user {user_id}: {e}")
        return set() # Return empty set on failure to prevent crashes

def sync_initial_state(user_id: int, open_trades_from_db: list):
    """
    Syncs the Redis cache with the state from the main database.
    This should be called on application startup.
    """
    try:
        client = get_redis_client()
        key = get_active_trades_key(user_id)
        
        # Get the current state from Redis
        redis_trades = client.smembers(key) or set()
        db_trades = {trade['symbol'] for trade in open_trades_from_db}

        # If they are already in sync, do nothing
        if redis_trades == db_trades:
            logger.info(f"[REDIS_SYNC] State for user {user_id} is already in sync. ({len(db_trades)} active trades)")
            return

        logger.info(f"[REDIS_SYNC] State mismatch for user {user_id}. Syncing from DB to Redis...")
        
        # Use a pipeline for an atomic transaction
        pipe = client.pipeline()
        pipe.delete(key) # Clear the existing set
        if db_trades:
            pipe.sadd(key, *db_trades) # Add all trades from the DB
        pipe.execute()
        
        logger.info(f"[REDIS_SYNC] Successfully synced state for user {user_id}. {len(db_trades)} trades are now active.")

    except Exception as e:
        logger.error(f"[REDIS_SYNC] Failed to sync initial state for user {user_id}: {e}", exc_info=True)

