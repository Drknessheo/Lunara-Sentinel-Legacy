"""
This is the main entry point for the Telegram bot.

It sets up the command handlers, schedules the background jobs, and starts the bot.
"""

import asyncio
import logging
import os

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from . import trade
from .core import autotrade_engine
from .utils import redis_utils
from . import config

logger = logging.getLogger(__name__)


def main() -> None:
    """Set up the bot and run it."""
    logger.info("Starting Lunara Bot...")

    # Ensure required configuration is present
    if not config.TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set!")
    if not config.REDIS_URL:
        raise ValueError("REDIS_URL is not set!")

    # --- Application Setup ---
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # --- Command Handlers ---
    application.add_handler(CommandHandler("start", trade.help_command))
    application.add_handler(CommandHandler("help", trade.help_command))
    application.add_handler(CommandHandler("about", trade.about_command))
    application.add_handler(CommandHandler("myprofile", trade.myprofile_command))
    application.add_handler(CommandHandler("setapi", trade.set_api_keys_command))
    application.add_handler(CommandHandler("close", trade.close_trade_command))
    application.add_handler(CommandHandler("clear_redis", trade.clear_redis_command))

    # --- Background Jobs ---
    job_queue = application.job_queue
    job_queue.run_repeating(
        autotrade_engine.autotrade_cycle,
        interval=config.AUTOTRADE_SCHEDULE_MINUTES * 60,
        first=10,  # Start after 10 seconds
    )
    logger.info(
        f"Autotrade cycle scheduled to run every {config.AUTOTRADE_SCHEDULE_MINUTES} minutes."
    )

    # --- Start the Bot ---
    logger.info("Starting bot polling...")
    application.run_polling()


if __name__ == "__main__":
    main()
