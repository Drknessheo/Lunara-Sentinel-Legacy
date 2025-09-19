import asyncio
import logging
import os
import sys
import threading

# --- Setup logging and path ---
if __package__:
    from . import logging_config
else:
    import logging_config

logging_config.setup_logging()

if not __package__:
    sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
else:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Ensure all local modules are imported correctly based on execution context
if __package__:
    from . import config, handlers, trade, trade_executor, web_server
    from . import db as new_db # The new thread-safe db module
    from .redis_persistence import RedisPersistence
else:
    import config
    import handlers
    import trade
    import trade_executor
    import web_server
    import db as new_db
    from redis_persistence import RedisPersistence

logger = logging.getLogger(__name__)

# --- Constants ---
ADMIN_ID = getattr(config, "ADMIN_USER_ID", None)

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
<code>/removecoins SYMBOL...</code> - Remove coins from your watchlist.

<b>⚙️ Autotrade Settings</b>
<code>/settings</code> - View all your autotrade settings.
<code>/settings autotrade on</code> - Enable or disable the autotrader.
<code>/settings trading_mode LIVE</code> - Set your trading mode (LIVE or PAPER).
<code>/settings trade_size_usdt 20</code> - Set the USDT value for each trade.
"""

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(HELP_MESSAGE)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_record, created = new_db.get_or_create_user(user.id)
    
    if created:
        logger.info(f"New user {user.id} ({user.username}) started the bot.")
    else:
        logger.info(f"Returning user {user.id} ({user.username}) started the bot.")

    welcome_message = (
        f"🌑 Welcome, {user.mention_html()}. The Lunara autotrader is at your command.\n\n"
        f"I will manage your trades based on the strategy we have designed.\n\n"
        f"<b>Key Commands:</b>\n<code>/myprofile</code> - View your portfolio and settings.\n<code>/help</code> - See all available commands.\n\n"
        f"Ensure your API keys are set and use <code>/settings</code> to configure your strategy."
    )
    await update.message.reply_html(welcome_message)

async def post_init(application: Application) -> None:
    logger.info("Running post-initialization setup...")
    await application.bot.delete_webhook(drop_pending_updates=True)
    if isinstance(application.persistence, RedisPersistence):
        await application.persistence.initialize()

    # --- LAUNCH THE NEW TRADE EXECUTOR ---
    logger.info("Initializing and starting the new TradeExecutor...")
    executor = trade_executor.TradeExecutor(application.bot)
    asyncio.create_task(executor.run())
    logger.info("TradeExecutor is now running in the background.")

async def post_shutdown(application: Application) -> None:
    logger.info("Running post-shutdown cleanup...")
    if isinstance(application.persistence, RedisPersistence):
        await application.persistence.shutdown()

def main() -> None:
    logger.info("🚀 Starting Lunara Bot with the new Trade Executor...")
    
    # --- Start the health-check web server in a background thread ---
    logger.info("Starting health-check web server in background...")
    web_server_thread = threading.Thread(target=web_server.run_web_server, daemon=True)
    web_server_thread.start()
    logger.info("Health-check web server is running.")

    # --- Assertions for core configuration ---
    assert config.TELEGRAM_BOT_TOKEN, "CRITICAL: TELEGRAM_BOT_TOKEN is not set!"
    assert config.REDIS_URL, "CRITICAL: REDIS_URL is not set!"
    assert config.ADMIN_USER_ID, "CRITICAL: ADMIN_USER_ID is not set!"

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

    # --- Command Handlers ---
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("myprofile", trade.myprofile_command))
    application.add_handler(CommandHandler("balance", trade.balance_command))
    application.add_handler(CommandHandler("quest", trade.quest_command))
    application.add_handler(CommandHandler("setapi", trade.set_api_keys_command))
    application.add_handler(CommandHandler("close", trade.close_trade_command))
    application.add_handler(CommandHandler("addcoins", trade.addcoins_command))
    application.add_handler(CommandHandler("removecoins", trade.removecoins_command))
    application.add_handler(CommandHandler("pay", handlers.pay_command))
    application.add_handler(CommandHandler("settings", trade.settings_command))

    logger.info("Starting bot polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
