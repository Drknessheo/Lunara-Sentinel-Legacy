import asyncio
import csv
import io
import json
import logging
import os
import sys
import time

# --- Setup logging and path ---
if __package__:
    from . import logging_config
else:
    import logging_config

logging_config.setup_logging()

import redis

if not __package__:
    sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
else:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

from datetime import datetime, timedelta, timezone

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import Conflict as TelegramConflict
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Ensure all local modules are imported correctly based on execution context
if __package__:
    from . import autotrade_jobs, config, handlers, redis_validator, slip_manager
    from . import db as new_db # The new thread-safe db module
    from .utils.redis_utils import delete_redis_slip, diagnose_slips_command
    from . import trade, trade_executor
    from .modules import db_access as db # Old db access
    from .redis_persistence import RedisPersistence
    from .Simulation import resonance_engine
    from .slip_parser import SlipParseError, parse_slip
else:
    import autotrade_jobs
    import config
    import handlers
    import redis_validator
    import slip_manager
    import db as new_db
    from utils.redis_utils import delete_redis_slip, diagnose_slips_command
    import trade
    import trade_executor
    from modules import db_access as db
    from redis_persistence import RedisPersistence
    from Simulation import resonance_engine
    from slip_parser import SlipParseError, parse_slip

logger = logging.getLogger(__name__)

# --- Constants ---
ADMIN_ID = getattr(config, "ADMIN_USER_ID", None)

# This HELP_MESSAGE is now updated to reflect the actual, consolidated commands.
HELP_MESSAGE = """🔮 <b>LunessaSignals Guide</b> 🔮

Your ultimate guide to mastering the crypto markets.

<b>🚀 Getting Started</b>
<code>/start</code> - Begin your journey.
<code>/myprofile</code> - View your profile, open trades, balances, and settings.
<code>/help</code> - Show this help message.

<b>🔗 Account & Wallet</b>
<code>/setapi KEY SECRET</code> - Link your Binance keys (in a private chat).
<code>/balance</code> - Check your LIVE or PAPER USDT balance.

<b>📈 Trading & Analysis</b>
<code>/quest SYMBOL</code> - Scan a crypto pair for opportunities.
<code>/close ID</code> - Manually close a trade by its ID.
<code>/addcoins SYMBOL...</code> - Add coins to your watchlist.

<b>⚙️ Settings</b>
<code>/settings</code> - View your current settings.
<code>/settings NAME VALUE</code> - Change a setting. Examples:
  <code>/settings autotrade on</code>
  <code>/settings trading_mode LIVE</code>

<b>🛡️ Admin Commands</b>
<code>/diagnose_slips</code> - [Admin] Check for corrupted trade slips.
"""

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(HELP_MESSAGE)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_record, created = new_db.get_or_create_user(user.id)
    
    if created:
        logger.info(f"New user {user.id} ({user.username}) started the bot.")
        if ADMIN_ID and context.bot:
            full_name = user.full_name.replace("[", "\\[").replace("`", "\\`")
            username = user.username.replace("_", "\\_") if user.username else 'N/A'

            announcement = (
                f"📣 New User Announcement 📣\n\n"
                f"*Name:* {full_name}\n"
                f"*Username:* @{username}\n"
                f"*User ID:* `{user.id}`"
            )
            try:
                await context.bot.send_message(chat_id=ADMIN_ID, text=announcement, parse_mode=ParseMode.MARKDOWN_V2)
            except Exception as e:
                logger.error(f"Failed to send new user announcement to admin: {e}")
    else:
        logger.info(f"Returning user {user.id} ({user.username}) started the bot.")

    welcome_message = (
        f"🌑 <b>A new trader emerges from the shadows.</b> {user.mention_html()}, you have been summoned by <b>Lunessa Shai'ra Gork</b>, Sorceress of DeFi and guardian of RSI gates.\n\n"
        f"Your journey begins now. I will monitor the markets for you, alert you to opportunities, and manage your trades.\n\n"
        f"<b>Key Commands:</b>\n<code>/quest SYMBOL</code> - Analyze a cryptocurrency.\n<code>/myprofile</code> - View your open trades and settings.\n<code>/help</code> - See all available commands.\n\n"
        f"To begin live trading, please provide your Binance API keys using the <code>/setapi</code> command in a private message with me."
    )
    await update.message.reply_html(welcome_message)

async def post_init(application: Application) -> None:
    logger.info("Running post-initialization setup...")
    await application.bot.delete_webhook(drop_pending_updates=True)
    if isinstance(application.persistence, RedisPersistence):
        await application.persistence.initialize()

async def post_shutdown(application: Application) -> None:
    logger.info("Running post-shutdown cleanup...")
    if isinstance(application.persistence, RedisPersistence):
        await application.persistence.shutdown()

def main() -> None:
    logger.info("🚀 Starting Lunara Bot...")

    # --- Assertions for core configuration ---
    assert config.TELEGRAM_BOT_TOKEN, "CRITICAL: TELEGRAM_BOT_TOKEN is not set!"
    assert config.REDIS_URL, "CRITICAL: REDIS_URL is not set!"
    assert config.ADMIN_USER_ID, "CRITICAL: ADMIN_USER_ID is not set!"
    assert config.SLIP_ENCRYPTION_KEY, "CRITICAL: SLIP_ENCRYPTION_KEY is not set!"

    # Initialize the old database (if needed, otherwise can be removed)
    db.initialize_database()

    # Initialize the new thread-safe database
    new_db.init_db()

    persistence = RedisPersistence(redis_url=config.REDIS_URL)
    application = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .persistence(persistence)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # --- Command Handlers (Now cleaned up) ---
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("myprofile", trade.myprofile_command))
    application.add_handler(CommandHandler("balance", trade.balance_command))
    application.add_handler(CommandHandler("quest", trade.quest_command))
    application.add_handler(CommandHandler("setapi", trade.set_api_keys_command))
    application.add_handler(CommandHandler("close", trade.close_trade_command))
    application.add_handler(CommandHandler("addcoins", trade.addcoins_command))
    application.add_handler(CommandHandler("pay", handlers.pay_command))
    application.add_handler(CommandHandler("settings", trade.settings_command))

    # Admin commands
    application.add_handler(CommandHandler("diagnose_slips", diagnose_slips_command, filters=filters.User(user_id=ADMIN_ID)))

    # --- Job Queue for background tasks ---
    job_queue = application.job_queue
    job_queue.run_repeating(autotrade_jobs.autotrade_cycle, interval=60, first=10)

    logger.info("Starting bot polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
