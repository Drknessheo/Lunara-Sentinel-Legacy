
import asyncio
import logging
import os
import sys

# --- Setup logging and path ---
# Ensure this runs from the project root for consistent imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src import logging_config
logging_config.setup_logging()

from src import db, config
from src.core import redis_client
from src.trade_executor import TradeExecutor
from telegram import Bot

logger = logging.getLogger(__name__)

async def run_single_cycle():
    """
    Performs a single, complete operational cycle for the bot and then exits.
    This is designed to be run as a Cron Job.
    """
    logger.info("[CRON_JOB] Awakening for a single cycle...")

    # --- Essential Config Assertions ---
    if not all([config.TELEGRAM_BOT_TOKEN, config.REDIS_URL, config.ADMIN_USER_ID]):
        logger.critical("[CRON_JOB] CRITICAL: Core environment variables are not set. Exiting.")
        return

    # --- Acquire Lock ---
    # This prevents multiple cron instances from running concurrently if one overruns.
    lock_acquired = redis_client.acquire_master_lock(lock_timeout=60) # Lock for 60s
    if not lock_acquired:
        logger.warning("[CRON_JOB] Another cycle is already in progress. Standing down.")
        return

    try:
        # --- Initialize Components ---
        await db.init_db()
        
        # We need a bot instance to pass to the executor for sending notifications
        # This does not start polling or webhooks.
        bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
        
        executor = TradeExecutor(bot)

        # --- Sync State ---
        # Ensures our Redis cache is aware of any trades made outside the cron's knowledge
        await executor._initial_state_sync()

        # --- Execute Core Logic ---
        logger.info("[CRON_JOB] Processing users with autotrade enabled...")
        user_ids = await db.get_users_with_autotrade_enabled()
        if user_ids:
            logger.info(f"[CRON_JOB] Found {len(user_ids)} users to process.")
            # Process users sequentially to manage resources
            for user_id in user_ids:
                await executor._process_user(user_id)
        else:
            logger.info("[CRON_JOB] No users with autotrade enabled found.")

        logger.info("[CRON_JOB] Cycle complete. The empire rests.")

    except Exception as e:
        logger.error(f"[CRON_JOB] An unhandled error occurred during the cycle: {e}", exc_info=True)
    finally:
        # --- Release Lock ---
        redis_client.release_master_lock()
        logger.info("[CRON_JOB] Lock released. Going back to sleep.")

if __name__ == "__main__":
    # Ensure a clean event loop for each run
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_single_cycle())
    finally:
        loop.close()

