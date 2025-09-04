import logging
import os

import redis

import config

if __package__:
    from .modules import db_access as db
else:
    from modules import db_access as db

logger = logging.getLogger(__name__)

# --- Redis Connection (lazy) ---
# Delay creating the Redis client until first use so importing this module
# doesn't attempt to connect during package import. Use get_redis_client()
# to retrieve a ready client or None if unavailable.
redis_client = None


def get_redis_client():
    global redis_client
    if redis_client is not None:
        return redis_client
    try:
        url = getattr(config, "REDIS_URL", None) or os.getenv("REDIS_URL")
        if not url:
            logger.debug("No REDIS_URL configured; redis validator will be disabled.")
            return None
        redis_client = redis.from_url(url, decode_responses=True)
        logger.info("Redis validator connected successfully.")
        return redis_client
    except Exception as e:
        logger.error(f"Redis validator failed to connect: {e}")
        redis_client = None
        return None


def validate_trade(slip_data: dict) -> tuple[bool, str]:
    """
    Validates a trade slip against all business rules.
    Returns a tuple: (is_valid: bool, reason: str)
    """
    rc = get_redis_client()
    if not rc:
        return False, "Validation failed: Redis connection is not available."

    user_id = slip_data["user_id"]
    symbol = slip_data["symbol"]

    # 1. Check for duplicates in Redis (Solves #3)
    redis_key = f"trade_status:{symbol}"
    if rc.exists(redis_key):
        return False, f"A trade for {symbol} is already being monitored in Redis."

    # 2. Check for duplicates in main DB from /import (Solves #2)
    if db.is_trade_open(user_id, symbol):
        return False, f"An open trade for {symbol} already exists in the database."

    # 3. Fetch user settings and validate slip rules (Solves #1 and #4)
    settings = db.get_user_effective_settings(user_id)

    # Validate risk percentage
    # Using STOP_LOSS_PERCENTAGE as the max risk for now, can be a separate setting later.
    max_risk = settings.get("STOP_LOSS_PERCENTAGE", 5.0)
    slip_risk = slip_data.get("risk_percent")

    if slip_risk > max_risk:
        reason = (
            f"Slip risk ({slip_risk}%) exceeds your max configured risk ({max_risk}%)."
        )
        logger.warning(f"Trade validation failed for user {user_id}: {reason}")
        return False, reason

    # --- Add more validations here as needed ---
    # Example: Check against a symbol whitelist or trade size limits

    logger.info(f"Trade validation successful for user {user_id} on {symbol}.")
    return True, "Validation successful."
