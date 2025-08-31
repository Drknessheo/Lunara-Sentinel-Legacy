import logging
import os
import sys

import redis

# Add src/ to sys.path if running from project root
sys.path.append(os.path.join(os.path.dirname(__file__), "src"))

import src.db  # This is still needed for get_trade_by_id and close_db_connection

# Now you can safely import helpers from the project
from security import encrypt_data  # Import encrypt_data

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Redis connection details
REDIS_URL = os.getenv(
    "REDIS_URL", "rediss://***:***@measured-whale-51756.upstash.io:6379"
)
try:
    r = redis.from_url(REDIS_URL)
    r.ping()  # Test connection
    logger.info("Successfully connected to Redis.")
except redis.exceptions.ConnectionError as e:
    logger.error(f"Could not connect to Redis: {e}")
    exit(1)

# SQLite database details
# DB_NAME is now directly imported from config

# Trade IDs to rehydrate (from the previous diagnostic)
trade_ids_to_rehydrate = [49, 51, 52, 54, 55, 58, 59]

logger.info("Starting Redis rehydration from SQLite...")

for trade_id in trade_ids_to_rehydrate:
    try:
        # Fetch trade from SQLite
        trade_data = src.db.get_trade_by_id(trade_id)

        if trade_data:
            quantity = str(trade_data["quantity"])  # Convert float to string
            status = trade_data["status"]

            encrypted_quantity = encrypt_data(quantity)  # Pass string directly
            encrypted_status = encrypt_data(status)  # Pass string directly

            # Restore keys in Redis
            r.set(f"trade:{trade_id}:quantity", encrypted_quantity)
            r.set(f"trade:{trade_id}:status", encrypted_status)

            logger.info(
                f"Trade {trade_id}: Successfully restored quantity='{quantity}' and status='{status}' in Redis."
            )
        else:
            logger.warning(
                f"Trade {trade_id}: Not found in SQLite database. Skipping rehydration."
            )

    except Exception as e:
        logger.error(f"Trade {trade_id}: An error occurred during rehydration: {e}")

# Close SQLite connection
src.db.close_db_connection()
logger.info("Redis rehydration process completed.")
