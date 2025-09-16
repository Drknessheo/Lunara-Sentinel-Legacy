import asyncio
import csv
import io
import json
import logging
import os
import sys
import time

# --- Setup logging and path ---
# This should be the very first thing to run
if __package__:
    from . import logging_config
else:
    import logging_config

logging_config.setup_logging()

import redis

# Ensure the src directory is on sys.path so imports work when running as a script
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

# Import config first
if __package__:
    from . import autotrade_jobs, config, redis_validator, slip_manager
else:
    import autotrade_jobs
    import config
    import redis_validator
    import slip_manager

try:
    import sys as _sys
    if "config" in globals():
        _sys.modules["config"] = config
except Exception:
    pass

ADMIN_ID = getattr(config, "ADMIN_USER_ID", None)

if __package__:
    from . import trade, trade_executor
    from .modules import db_access as db
    from .redis_persistence import RedisPersistence
    from .Simulation import resonance_engine
    from .slip_parser import SlipParseError, parse_slip
else:
    import trade
    import trade_executor
    from modules import db_access as db
    from redis_persistence import RedisPersistence
    from Simulation import resonance_engine
    from slip_parser import SlipParseError, parse_slip

logger = logging.getLogger(__name__)

# Transmuted to HTML for resilience and clarity
HELP_MESSAGE = """🔮 <b>LunessaSignals Guide</b> 🔮

Your ultimate guide to mastering the crypto markets.

<b>🚀 Getting Started</b>
<code>/start</code> - Begin your journey.
<code>/myprofile</code> - View your profile and settings.
<code>/subscribe</code> - See premium benefits and how to upgrade.
<code>/learn</code> - Get quick educational tips.
<code>/help</code> - Show this help message.

<b>🔗 Account & Wallet</b>
<code>/setapi KEY SECRET</code> - Link your Binance keys (in a private chat).
<code>/linkbinance</code> - Instructions for creating secure API keys.
<code>/wallet</code> - View your full Binance Spot Wallet.
<code>/balance</code> - Check your LIVE or PAPER balance.

<b>📈 Trading & Analysis</b>
<code>/quest SYMBOL</code> - Scan a crypto pair for opportunities.
<code>/status</code> - View your open trades and watchlist.
<code>/close ID</code> - Manually complete a quest (trade).
<code>/import SYMBOL [PRICE]</code> - Log an existing trade.
<code>/papertrade</code> - Toggle practice mode.
<code>/addcoins SYMBOL1 SYMBOL2...</code> - Add coins to your watchlist.

<b>✨ Performance & Community</b>
<code>/review</code> - See your personal performance stats.
<code>/top_trades</code> - View your 3 best trades.
<code>/leaderboard</code> - See the global top 3 trades.
<code>/resonate</code> - A word of wisdom from LunessaSignals.
<code>/referral</code> - Get your referral link to invite friends.

<b>🛠️ Utilities</b>
<code>/ask QUESTION</code> - Ask the AI Oracle about trading.
<code>/safety</code> - Read important trading advice.
<code>/pay</code> - See how to support LunessaSignals's development.

<b>🛡️ Admin Commands</b>
<code>/autotrade on | off</code> - [Admin] Enable or disable automatic trading for all users.
<code>/binance_status</code> - [Admin] Check the connection status to the Binance API.
<code>/diagnose_slips</code> - [Admin] Run a diagnostic check on the slips database to identify corrupted data.
<code>/settings</code> - [Admin] Customize global trading parameters.
"""

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays a help message with all available commands, using HTML formatting."""
    await update.message.reply_html(HELP_MESSAGE)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message and registers the user if they are new."""
    user = update.effective_user
    db.get_or_create_user(user.id)
    logger.info(f"User {user.id} ({user.username}) started the bot.")
    welcome_message = (
        f"🌑 <b>A new trader emerges from the shadows.</b> {user.mention_html()}, you have been summoned by <b>Lunessa Shai'ra Gork</b>, Sorceress of DeFi and guardian of RSI gates.\n\n"
        f"Your journey begins now. I will monitor the markets for you, alert you to opportunities, and manage your trades.\n\n"
        f"<b>Key Commands:</b>\n<code>/quest SYMBOL</code> - Analyze a cryptocurrency.\n<code>/status</code> - View your open trades and watchlist.\n<code>/help</code> - See all available commands.\n\n"
        f"To unlock live trading, please provide your Binance API keys using the <code>/setapi</code> command in a private message with me."
    )
    await update.message.reply_html(welcome_message)


async def myprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the user's profile information, including tier and settings."""
    user_id = update.effective_user.id
    user_record = db.get_user(user_id)
    if not user_record:
        await update.message.reply_text("Could not find your profile. Please try /start.")
        return

    # This correctly uses the existing logic without modification
    settings = db.get_user_effective_settings(user_id)
    trading_mode, paper_balance = db.get_user_trading_mode_and_balance(user_id)
    
    username = update.effective_user.username or "(not set)"
    autotrade = "Enabled" if settings.get('AUTOTRADE_ENABLED') else "Disabled"
    
    message = f"""<b>Your Profile</b>

<b>User ID:</b> <code>{user_id}</code>
<b>Username:</b> @{username}
<b>Tier:</b> {user_record['tier']}
<b>Trading Mode:</b> {trading_mode}
<b>Autotrade:</b> {autotrade}"""
    
    if trading_mode == "LIVE":
        message += "\n<b>USDT Balance:</b> (see /wallet)"
    else:
        message += f"\n<b>Paper Balance:</b> ${paper_balance:,.2f}"
    
    message += "\n\n<b>Effective Settings:</b>"
    message += f"\n- RSI Buy: {settings.get('RSI_BUY_THRESHOLD', 'N/A')}"
    message += f"\n- RSI Sell: {settings.get('RSI_SELL_THRESHOLD', 'N/A')}"
    message += f"\n- Stop Loss: {settings.get('STOP_LOSS_PERCENTAGE', 'N/A')}%"
    message += f"\n- Trailing Activation: {settings.get('TRAILING_PROFIT_ACTIVATION_PERCENT', 'N/A')}%"
    message += f"\n- Trailing Drop: {settings.get('TRAILING_STOP_DROP_PERCENT', 'N/A')}%"
    message += f"\n- Trade Size (USDT): {settings.get('TRADE_SIZE_USDT', 'N/A')}"

    await update.message.reply_html(message)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for the /status command. Shows subscription status, open quests, and watched symbols."""
    user_id = update.effective_user.id
    user_record = db.get_user(user_id)
    if not user_record:
        await update.message.reply_text("Could not find your profile. Please try /start.")
        return

    tier = user_record['tier']
    expires_str = user_record['subscription_expires']
    autotrade_status = "✅ Enabled" if db.get_user_effective_settings(user_id).get('AUTOTRADE_ENABLED') else "❌ Disabled"

    subscription_message = f"👤 <b>Subscription Status</b>\n- Tier: <b>{tier.capitalize()}</b>\n- Auto-trade: {autotrade_status}\n"

    if tier != "FREE" and expires_str:
        try:
            expires_dt = datetime.fromisoformat(expires_str).astimezone(timezone.utc)
            now_utc = datetime.now(timezone.utc)
            if expires_dt > now_utc:
                days_remaining = (expires_dt - now_utc).days
                expiry_date_formatted = expires_dt.strftime("%d %b %Y")
                subscription_message += f"- Expires: <b>{expiry_date_formatted}</b> ({days_remaining} days left)\n"
            else:
                subscription_message += "- Status: <b>Expired</b>\n"
        except (ValueError, TypeError):
            subscription_message += "- Expiry: <i>Not set</i>\n"

    subscription_message += "\n" + ("-" * 20) + "\n\n"

    open_trades = db.get_open_trades(user_id)
    if not open_trades:
        await update.message.reply_html(
            subscription_message + "You have no open quests. Use <code>/quest</code> to find an opportunity."
        )
        return
        
    message = ""
    for trade_item in open_trades:
        symbol = trade_item["coin_symbol"]
        buy_price = trade_item["buy_price"]
        current_price = trade.get_current_price(symbol)
        trade_id = trade_item["id"]
        
        message += f"\n🔹 <b>{symbol}</b> (ID: {trade_id})"

        if current_price:
            pnl_percent = ((current_price - buy_price) / buy_price) * 100
            pnl_emoji = "📈" if pnl_percent >= 0 else "📉"
            message += (
                f"\n   {pnl_emoji} P/L: <code>{pnl_percent:+.2f}%</code>"
                f"\n   Bought: <code>${buy_price:,.8f}</code>"
                f"\n   Current: <code>${current_price:,.8f}</code>"
            )
    
    await update.message.reply_html(subscription_message + message)

async def quest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for the /quest command. Calls the trade module."""
    # Correctly call the main quest command in trade.py which handles all logic
    await trade.quest_command(update, context)

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the global leaderboard of top trades."""
    top_trades = db.get_global_top_trades(limit=3)

    if not top_trades:
        await update.message.reply_html(
            "The Hall of Legends is still empty. No legendary quests have been completed yet!"
        )
        return

    message = "🏆 <b>Hall of Legends: Global Top Quests</b> 🏆\n\n<i>These are the most glorious victories across the realm:</i>\n\n"
    rank_emojis = ["🥇", "🥈", "🥉"]

    for i, trade_entry in enumerate(top_trades):
        emoji = rank_emojis[i] if i < len(rank_emojis) else "🔹"
        user_id = trade_entry["user_id"]
        user_name = "A mysterious adventurer"
        try:
            chat = await context.bot.get_chat(user_id)
            user_name = chat.first_name
        except Exception as e:
            logger.warning(f"Could not fetch user name for {user_id} for leaderboard: {e}")

        message += f"{emoji} <b>{trade_entry['coin_symbol']}</b>: <code>{trade_entry['pnl_percent']:+.2f}%</code> (by {user_name})\n"

    message += "\nWill your name be etched into legend?"
    await update.message.reply_html(message)

async def import_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Placeholder for the /import command, as requested."""
    await update.message.reply_text("This command is not yet implemented. It will be used to log an existing trade.")

async def close_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Closes an open trade by its ID by delegating to the trade module."""
    # This now correctly calls the trade module's close command
    await trade.close_trade_command(update, context)

async def review_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reviews the user's completed trade performance."""
    user_id = update.effective_user.id
    closed_trades = db.get_closed_trades(user_id)

    if not closed_trades:
        await update.message.reply_text("You have no completed trades to review.")
        return

    wins = sum(1 for t in closed_trades if t['win_loss'] == 'win')
    losses = len(closed_trades) - wins
    total_pnl = sum(t['pnl_percentage'] for t in closed_trades)
    win_rate = (wins / len(closed_trades)) * 100 if closed_trades else 0
    avg_pnl = total_pnl / len(closed_trades) if closed_trades else 0

    message = f"""🌟 <b>Performance Review</b> 🌟

<b>Completed Quests:</b> {len(closed_trades)}
<b>Wins:</b> {wins}
<b>Losses:</b> {losses}
<b>Win Rate:</b> {win_rate:.2f}%
<b>Average P/L:</b> <code>{avg_pnl:,.2f}%</code>
"""
    await update.message.reply_html(message)


async def top_trades_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the user's top 3 most profitable closed trades."""
    user_id = update.effective_user.id
    top_trades = db.get_user_top_trades(user_id, limit=3)

    if not top_trades:
        await update.message.reply_text("You have no completed profitable quests to rank.")
        return

    message = "🏆 <b>Your Hall of Fame</b> 🏆\n\n<i>Here are your most legendary victories:</i>\n\n"
    rank_emojis = ["🥇", "🥈", "🥉"]

    for i, trade_entry in enumerate(top_trades):
        emoji = rank_emojis[i] if i < len(rank_emojis) else "🔹"
        message += f"{emoji} <b>{trade_entry['coin_symbol']}</b>: <code>{trade_entry['pnl_percent']:+.2f}%</code>\n"

    await update.message.reply_html(message)

async def autotrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to control the AI autotrading feature."""
    # This correctly delegates to the trade module
    await trade.autotrade_command(update, context)

async def post_init(application: Application) -> None:
    """Runs once after the bot is initialized."""
    logger.info("Running post-initialization setup...")
    await application.bot.delete_webhook(drop_pending_updates=True)
    if isinstance(application.persistence, RedisPersistence):
        await application.persistence.initialize()

async def post_shutdown(application: Application) -> None:
    """Runs once before the bot shuts down."""
    logger.info("Running post-shutdown cleanup...")
    if isinstance(application.persistence, RedisPersistence):
        await application.persistence.shutdown()

def main() -> None:
    """Set up the bot and run it."""
    logger.info("🚀 Starting Lunara Bot...")

    assert config.TELEGRAM_BOT_TOKEN, "❌ TELEGRAM_BOT_TOKEN is not set!"
    assert os.getenv("REDIS_URL"), "❌ REDIS_URL is missing!"

    db.initialize_database()

    persistence = RedisPersistence(redis_url=os.getenv("REDIS_URL"))
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
    application.add_handler(CommandHandler("myprofile", myprofile_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("quest", quest_command))
    application.add_handler(CommandHandler("leaderboard", leaderboard_command))
    application.add_handler(CommandHandler("import", import_command))
    application.add_handler(CommandHandler("close", close_command))
    application.add_handler(CommandHandler("review", review_command))
    application.add_handler(CommandHandler("top_trades", top_trades_command))
    application.add_handler(CommandHandler("autotrade", autotrade_command))
    
    # --- Other command handlers from trade.py or this file ---
    application.add_handler(CommandHandler("balance", trade.balance_command))
    application.add_handler(CommandHandler("setapi", trade.set_api_keys_command))
    application.add_handler(CommandHandler("addcoins", trade.addcoins_command))
    application.add_handler(CommandHandler("binance_status", trade.binance_status_command))


    # --- Job Queue ---
    job_queue = application.job_queue
    job_queue.run_repeating(autotrade_jobs.monitor_autotrades, interval=60, first=10)


    logger.info("Starting bot polling...")
    application.run_polling()


if __name__ == "__main__":
    main()
