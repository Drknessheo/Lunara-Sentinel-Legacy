
import asyncio
import logging
import os
import sys

# --- Setup logging and path ---
if __package__:
    from . import logging_config
else:
    import logging_config

logging_config.setup_logging()

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

# --- Import Core Components ---
from src import config
from src import db
from src import handlers
from src.trade_executor import TradeExecutor
from src.redis_persistence import RedisPersistence
from src.core import redis_client # Import the redis_client

logger = logging.getLogger(__name__)

async def main() -> None:
    """The asynchronous main function to rule them all."""
    logger.info("🚀 Forging the new asynchronous empire...")

    # --- Assertions for core configuration ---
    assert config.TELEGRAM_BOT_TOKEN, "CRITICAL: TELEGRAM_BOT_TOKEN is not set!"
    assert config.REDIS_URL, "CRITICAL: REDIS_URL is not set!"
    assert config.ADMIN_USER_ID, "CRITICAL: ADMIN_USER_ID is not set!"

    # --- Asynchronous Initialization ---
    logger.info("Initializing the asynchronous database...")
    await db.init_db()
    logger.info("Database initialization complete.")

    # --- DIGITAL CEASEFIRE: Acquire lock or stand down ---
    lock_acquired = redis_client.acquire_master_lock()
    if not lock_acquired:
        logger.warning("Another bot instance is active. This instance will stand down.")
        # Optional: could sleep and retry, but for Render's restarts, exiting is cleaner.
        return # Exit gracefully

    # --- Build Application ---
    persistence = RedisPersistence(redis_url=config.REDIS_URL)
    
    application = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .persistence(persistence)
        .build()
    )

    # --- Register the Corrected and Completed Asynchronous Handlers ---
    application.add_handler(CommandHandler("start", handlers.start_command))
    application.add_handler(CommandHandler("help", handlers.help_command))
    application.add_handler(CommandHandler("status", handlers.status_command))
    application.add_handler(CommandHandler("myprofile", handlers.myprofile_command))
    application.add_handler(CommandHandler("settings", handlers.settings_command))
    application.add_handler(CommandHandler("pay", handlers.pay_command))
    
    application.add_handler(CallbackQueryHandler(handlers.settings_callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.message_handler))
    application.add_error_handler(handlers.error_handler)

    # --- Start Background Tasks --- 
    logger.info("Initializing and starting the TradeExecutor as a background task...")
    executor = TradeExecutor(application.bot)
    executor_task = asyncio.create_task(executor.run())
    application.bot_data['executor_task'] = executor_task
    logger.info("TradeExecutor is now running in the background.")

    # --- Run the Bot ---
    try:
        logger.info("Starting bot polling... The empire is listening.")
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        
        # Keep the master lock renewed
        while True:
            redis_client.renew_master_lock()
            await asyncio.sleep(10) # Renew lock every 10 seconds

    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown signal received.")
    finally:
        logger.info("Beginning graceful shutdown...")
        redis_client.release_master_lock() # Release the lock on shutdown
        if application.updater and application.updater.running:
            await application.updater.stop()
        if application.running:
            await application.stop()
        if not executor_task.done():
            executor_task.cancel()
            try:
                await executor_task
            except asyncio.CancelledError:
                logger.info("TradeExecutor task successfully cancelled.")
        logger.info("Empire has been laid to rest. Goodbye.")

if __name__ == "__main__":
    asyncio.run(main())
