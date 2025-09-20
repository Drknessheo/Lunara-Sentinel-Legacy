
import redis
import logging
import json
import time
from .. import config

logger = logging.getLogger(__name__)

# --- Constants ---
STRATEGIC_RETREAT_PERIODS = [3600, 10800, 21600, 32400]  # 1h, 3h, 6h, 9h
MASTER_LOCK_KEY = "LUNARA_LEGION_MASTER_LOCK"
LOCK_EXPIRY_SECONDS = 30  # Increased expiry for more stability
instance_id = f"legion_instance_{time.time()}" # A unique ID for this bot instance

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
    if redis_client is None:
        raise ConnectionError("Redis client is not available.")
    return redis_client

# --- Master Lock Protocol ---
def acquire_master_lock() -> bool:
    """Attempts to acquire the master lock for this bot instance."""
    try:
        client = get_redis_client()
        # Atomically set the key if it does not exist, with an expiry
        logger.info(f"Instance {instance_id} attempting to acquire master lock...")
        is_acquired = client.set(MASTER_LOCK_KEY, instance_id, ex=LOCK_EXPIRY_SECONDS, nx=True)
        if is_acquired:
            logger.info(f"Instance {instance_id} has acquired the master lock. We are the Prime Legion.")
        else:
            current_holder = client.get(MASTER_LOCK_KEY)
            logger.warning(f"Instance {instance_id} failed to acquire lock. Prime Legion is {current_holder}.")
        return is_acquired
    except Exception as e:
        logger.error(f"[REDIS_LOCK] Failed to acquire master lock: {e}")
        return False # Fail safe

def renew_master_lock():
    """Renews the master lock if this instance still holds it."""
    try:
        client = get_redis_client()
        # Use a transaction to ensure we only renew our own lock
        pipe = client.pipeline()
        pipe.watch(MASTER_LOCK_KEY)
        if pipe.get(MASTER_LOCK_KEY) == instance_id:
            pipe.multi()
            pipe.expire(MASTER_LOCK_KEY, LOCK_EXPIRY_SECONDS)
            pipe.execute()
            logger.debug(f"Master lock renewed by {instance_id}.")
        pipe.unwatch()
    except Exception as e:
        logger.error(f"[REDIS_LOCK] Failed to renew master lock by {instance_id}: {e}")

def release_master_lock():
    """Releases the master lock if this instance holds it."""
    try:
        client = get_redis_client()
        # Use a transaction to ensure we only delete our own lock
        pipe = client.pipeline()
        pipe.watch(MASTER_LOCK_KEY)
        if pipe.get(MASTER_LOCK_KEY) == instance_id:
            pipe.multi()
            pipe.delete(MASTER_LOCK_KEY)
            pipe.execute()
            logger.info(f"Master lock released by {instance_id}.")
        pipe.unwatch()
    except Exception as e:
        logger.error(f"[REDIS_LOCK] Failed to release master lock by {instance_id}: {e}")


# --- Key Generation ---
def get_key(namespace: str, user_id: int, identifier: str = "") -> str:
    # Create a consistent hash for identifiers to keep keys clean
    id_hash = hash(identifier) if identifier else ''
    return f"user:{user_id}:{namespace}:{id_hash}"

# --- Active Trade Management ---
def add_active_trade(user_id: int, symbol: str):
    try:
        client = get_redis_client()
        client.sadd(get_key("active_trades", user_id), symbol)
    except Exception as e:
        logger.error(f"[REDIS] Failed to add active trade for user {user_id}: {e}")

def remove_active_trade(user_id: int, symbol: str):
    try:
        client = get_redis_client()
        client.srem(get_key("active_trades", user_id), symbol)
    except Exception as e:
        logger.error(f"[REDIS] Failed to remove active trade for user {user_id}: {e}")

def get_active_trades(user_id: int) -> set:
    try:
        client = get_redis_client()
        return client.smembers(get_key("active_trades", user_id)) or set()
    except Exception as e:
        logger.error(f"[REDIS] Failed to get active trades for user {user_id}: {e}")
        return set()

def sync_initial_state(user_id: int, open_trades_from_db: list):
    try:
        client = get_redis_client()
        key = get_key("active_trades", user_id)
        db_trades = {trade['symbol'] for trade in open_trades_from_db}
        # This check is crucial to avoid race conditions on startup
        if client.smembers(key) == db_trades:
            return

        logger.info(f"[REDIS_SYNC] Syncing state for user {user_id}...")
        with client.pipeline() as pipe:
            pipe.delete(key)
            if db_trades:
                pipe.sadd(key, *db_trades)
            pipe.execute()
    except Exception as e:
        logger.error(f"[REDIS_SYNC] Failed to sync state for user {user_id}: {e}")

# --- Oracle Cooldown & Caching Protocol ---
def is_gemini_cooldown_active(user_id: int) -> bool:
    """Checks if the cooldown period for Gemini API calls is active for a user."""
    try:
        client = get_redis_client()
        return client.exists(get_key("gemini_cooldown", user_id))
    except Exception as e:
        logger.error(f"[REDIS_COOLDOWN] Failed to check cooldown for {user_id}: {e}")
        return True  # Fail safe: assume cooldown is active

def set_gemini_cooldown(user_id: int):
    """Activates the Gemini API call cooldown for a user."""
    try:
        client = get_redis_client()
        cooldown_seconds = config.AI_TRADE_INTERVAL_MINUTES * 60
        client.set(get_key("gemini_cooldown", user_id), "active", ex=cooldown_seconds)
    except Exception as e:
        logger.error(f"[REDIS_COOLDOWN] Failed to set cooldown for {user_id}: {e}")

def get_gemini_decision_cache(user_id: int, symbols: list[str]) -> dict | None:
    try:
        client = get_redis_client()
        key = get_key("gemini_cache", user_id, identifier=str(tuple(sorted(symbols))))
        cached = client.get(key)
        if cached:
            logger.info(f"[SCRIBE] Cache hit for user {user_id}. The Headmaster rests.")
            return json.loads(cached)
        return None
    except Exception as e:
        logger.error(f"[SCRIBE] Failed to retrieve cached decisions: {e}")
        return None

def set_gemini_decision_cache(user_id: int, symbols: list[str], decisions: dict):
    try:
        client = get_redis_client()
        symbols_tuple = tuple(sorted(symbols))
        cache_key = get_key("gemini_cache", user_id, identifier=str(symbols_tuple))
        failure_key = get_key("gemini_failures", user_id, identifier=str(symbols_tuple))
        
        # Cache successful decision, standard short-term expiry
        client.set(cache_key, json.dumps(decisions), ex=config.AI_TRADE_INTERVAL_MINUTES * 60)
        # On success, clear any previous failure tracking
        client.delete(failure_key)

    except Exception as e:
        logger.error(f"[SCRIBE] Failed to cache successful decision: {e}")

def cache_gemini_failure(user_id: int, symbols: list[str]) -> dict:
    decisions = {s: "HOLD" for s in symbols}
    try:
        client = get_redis_client()
        symbols_tuple = tuple(sorted(symbols))
        failure_key = get_key("gemini_failures", user_id, identifier=str(symbols_tuple))
        cache_key = get_key("gemini_cache", user_id, identifier=str(symbols_tuple))
        
        failure_count = client.incr(failure_key)
        retreat_index = min(failure_count - 1, len(STRATEGIC_RETREAT_PERIODS) - 1)
        retreat_seconds = STRATEGIC_RETREAT_PERIODS[retreat_index]
        
        client.expire(failure_key, retreat_seconds)
        client.set(cache_key, json.dumps(decisions), ex=retreat_seconds)

        logger.warning(f"[SCRIBE] Oracle failure {failure_count}. Caching HOLD and retreating for {retreat_seconds / 60:.0f} mins.")
        return decisions
    except Exception as e:
        logger.error(f"[SCRIBE] Critical error during failure caching: {e}")
        return decisions
