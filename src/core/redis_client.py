
import redis
import logging
import json
from .. import config

logger = logging.getLogger(__name__)

# --- Exponential Backoff for Oracle Failures (in seconds) ---
# 1-hour, 3-hours, 6-hours, 9-hours
STRATEGIC_RETREAT_PERIODS = [3600, 10800, 21600, 32400]

redis_client = None

try:
    if config.USE_FAKE_REDIS:
        import fakeredis
        redis_client = fakeredis.FakeRedis(decode_responses=True)
        logger.info("Using FakeRedis for in-memory session management.")
    else:
        redis_client = redis.from_url(config.REDIS_URL, decode_responses=True)
        redis_client.ping()
        logger.info("Successfully connected to Redis server.")

except Exception as e:
    logger.critical(f"An unexpected error occurred while initializing Redis: {e}", exc_info=True)
    redis_client = None

def get_redis_client():
    """Returns the configured Redis client instance."""
    if redis_client is None:
        raise ConnectionError("Redis client is not available. The application cannot proceed.")
    return redis_client

# --- Key Generation ---
def get_active_trades_key(user_id: int) -> str:
    return f"user:{user_id}:active_trades"

def get_gemini_cache_key(user_id: int, symbols_tuple: tuple) -> str:
    """Creates a consistent key for caching Gemini SUCCESS decisions."""
    return f"user:{user_id}:gemini_cache:{hash(symbols_tuple)}"

def get_gemini_failure_key(user_id: int, symbols_tuple: tuple) -> str:
    """Creates a consistent key for tracking Gemini FAILURE counts."""
    return f"user:{user_id}:gemini_failures:{hash(symbols_tuple)}"

# --- Active Trade Management ---
def add_active_trade(user_id: int, symbol: str):
    try:
        client = get_redis_client()
        client.sadd(get_active_trades_key(user_id), symbol)
        logger.info(f"[REDIS] Added {symbol} to active trades for user {user_id}.")
    except Exception as e:
        logger.error(f"[REDIS] Failed to add active trade for user {user_id}: {e}")

def remove_active_trade(user_id: int, symbol: str):
    try:
        client = get_redis_client()
        client.srem(get_active_trades_key(user_id), symbol)
        logger.info(f"[REDIS] Removed {symbol} from active trades for user {user_id}.")
    except Exception as e:
        logger.error(f"[REDIS] Failed to remove active trade for user {user_id}: {e}")

def get_active_trades(user_id: int) -> set:
    try:
        client = get_redis_client()
        return client.smembers(get_active_trades_key(user_id)) or set()
    except Exception as e:
        logger.error(f"[REDIS] Failed to get active trades for user {user_id}: {e}")
        return set()

def sync_initial_state(user_id: int, open_trades_from_db: list):
    try:
        client = get_redis_client()
        key = get_active_trades_key(user_id)
        redis_trades = client.smembers(key) or set()
        db_trades = {trade['symbol'] for trade in open_trades_from_db}
        if redis_trades == db_trades:
            return

        logger.info(f"[REDIS_SYNC] State mismatch for user {user_id}. Syncing from DB to Redis...")
        with client.pipeline() as pipe:
            pipe.delete(key)
            if db_trades:
                pipe.sadd(key, *db_trades)
            pipe.execute()
        logger.info(f"[REDIS_SYNC] Successfully synced state for user {user_id}.")
    except Exception as e:
        logger.error(f"[REDIS_SYNC] Failed to sync initial state for user {user_id}: {e}", exc_info=True)


# --- Imperial Scribe: Caching & Strategic Retreat Protocol ---
def get_gemini_decision_cache(user_id: int, symbols: list[str]) -> dict | None:
    """Retrieves cached Gemini decisions (both success and failure) if available."""
    try:
        client = get_redis_client()
        symbols_tuple = tuple(sorted(symbols))
        key = get_gemini_cache_key(user_id, symbols_tuple)
        cached_decisions = client.get(key)
        if cached_decisions:
            logger.info(f"[SCRIBE] Cache hit for user {user_id} and symbols {symbols_tuple}. The Headmaster rests.")
            return json.loads(cached_decisions)
        return None
    except Exception as e:
        logger.error(f"[SCRIBE] Failed to retrieve cached decisions: {e}")
        return None

def set_gemini_decision_cache(user_id: int, symbols: list[str], decisions: dict):
    """Caches a SUCCESSFUL Gemini decision and resets any failure tracking."""
    try:
        client = get_redis_client()
        symbols_tuple = tuple(sorted(symbols))
        
        # Cache the successful decision with the standard short-term expiry
        cache_key = get_gemini_cache_key(user_id, symbols_tuple)
        client.set(cache_key, json.dumps(decisions), ex=config.AI_TRADE_INTERVAL_MINUTES * 60)
        logger.info(f"[SCRIBE] Cached successful Gemini decisions for user {user_id}.")

        # On success, clear the failure counter
        failure_key = get_gemini_failure_key(user_id, symbols_tuple)
        client.delete(failure_key)
        logger.debug(f"[SCRIBE] Cleared failure count for user {user_id} on successful call.")

    except Exception as e:
        logger.error(f"[SCRIBE] Failed to cache successful decision: {e}")

def cache_gemini_failure(user_id: int, symbols: list[str]) -> dict:
    """Handles a FAILED Gemini API call by caching a HOLD decision with exponential backoff."""
    decisions = {s: "HOLD" for s in symbols}
    try:
        client = get_redis_client()
        symbols_tuple = tuple(sorted(symbols))
        
        # Increment failure count and get the new count
        failure_key = get_gemini_failure_key(user_id, symbols_tuple)
        failure_count = client.incr(failure_key)
        
        # Determine the retreat period. Use the last period for counts beyond the list.
        retreat_index = min(failure_count - 1, len(STRATEGIC_RETREAT_PERIODS) - 1)
        retreat_seconds = STRATEGIC_RETREAT_PERIODS[retreat_index]

        # Set the failure key's TTL to the retreat period so it eventually resets
        client.expire(failure_key, retreat_seconds)

        # Cache a "HOLD" decision for the duration of the retreat
        cache_key = get_gemini_cache_key(user_id, symbols_tuple)
        client.set(cache_key, json.dumps(decisions), ex=retreat_seconds)

        logger.warning(f"[SCRIBE] Oracle failure count {failure_count} for user {user_id}. Caching HOLD and retreating for {retreat_seconds // 60} minutes.")
        return decisions
    except Exception as e:
        logger.error(f"[SCRIBE] Critical error during failure caching: {e}")
        return decisions
