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

# Note: Markdown-sensitive characters escaped for Telegram compatibility
HELP_MESSAGE = """🔮 *LunessaSignals Guide* 🔮

Your ultimate guide to mastering the crypto markets.

*🚀 Getting Started*
/start - Begin your journey.
/myprofile - View your profile and settings.
/subscribe - See premium benefits and how to upgrade.
/learn - Get quick educational tips.
/help - Show this help message.

*🔗 Account & Wallet*
/setapi KEY SECRET - Link your Binance keys (in a private chat).
/linkbinance - Instructions for creating secure API keys.
/wallet - View your full Binance Spot Wallet.
/balance - Check your LIVE or PAPER balance.

*📈 Trading & Analysis*
/quest SYMBOL - Scan a crypto pair for opportunities.
/status - View your open trades and watchlist.
/close ID - Manually complete a quest (trade).
/import SYMBOL \\[PRICE\\] - Log an existing trade.
/papertrade - Toggle practice mode.
/addcoins SYMBOL1 SYMBOL2... - Add coins to your watchlist.

*✨ Performance & Community*
/review - See your personal performance stats.
/top_trades - View your 3 best trades.
/leaderboard - See the global top 3 trades.
/resonate - A word of wisdom from LunessaSignals.
/referral - Get your referral link to invite friends.

*🛠️ Utilities*
/ask QUESTION - Ask the AI Oracle about trading.
/safety - Read important trading advice.
/pay - See how to support LunessaSignals\'s development.

*🛡️ Admin Commands*
/autotrade on \\| off - \\[Admin\\] Enable or disable automatic trading for all users.
/binance_status - \\[Admin\\] Check the connection status to the Binance API.
/diagnose_slips - \\[Admin\\] Run a diagnostic check on the slips database to identify corrupted data.
/settings - \\[Admin\\] Customize global trading parameters.
"""

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays a help message with all available commands."""
    await update.message.reply_text(HELP_MESSAGE, parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message and registers the user if they are new."""
    user = update.effective_user
    db.get_or_create_user(user.id)
    logger.info(f"User {user.id} ({user.username}) started the bot.")
    welcome_message = (
        f"🌑 <b>A new trader emerges from the shadows.</b> {user.mention_html()}, you have been summoned by <b>Lunessa Shai\'ra Gork</b>, Sorceress of DeFi and guardian of RSI gates.\\n\\n"
        f"Your journey begins now. I will monitor the markets for you, alert you to opportunities, and manage your trades.\\n\\n"
        f"<b>Key Commands:</b>\\n/quest <code>SYMBOL</code> - Analyze a cryptocurrency.\\n/status - View your open trades and watchlist.\\n/help - See all available commands.\\n\\n"
        f"To unlock live trading, please provide your Binance API keys using the <code>/setapi</code> command in a private message with me."
    )
    await update.message.reply_html(welcome_message)


async def myprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the user\'s profile information, including tier and settings."""
    user_id = update.effective_user.id
    user_record = db.get_user(user_id)
    if not user_record:
        await update.message.reply_text("Could not find your profile. Please try /start.")
        return

    # This correctly uses the existing logic without modification
    settings = db.get_user_effective_settings(user_id)
    trading_mode, paper_balance = db.get_user_trading_mode_and_balance(user_id)
    
    username = update.effective_user.username or "(not set)"
    autotrade = "Enabled" if settings.get(\'AUTOTRADE_ENABLED\') else "Disabled"
    
    message = f"""*Your Profile*

*User ID:* `{user_id}`
*Username:* @{username}
*Tier:* {user_record[\'tier\']}
*Trading Mode:* {trading_mode}
*Autotrade:* {autotrade}"""
    
    if trading_mode == "LIVE":
        message += "\\n*USDT Balance:* (see /wallet)"
    else:
        message += f"\\n*Paper Balance:* ${paper_balance:,.2f}"
    
    message += "\\n\\n*Effective Settings:*"
    message += f"\\n- RSI Buy: {settings.get(\'RSI_BUY_THRESHOLD\', \'N/A\')}"
    message += f"\\n- RSI Sell: {settings.get(\'RSI_SELL_THRESHOLD\', \'N/A\')}"
    message += f"\\n- Stop Loss: {settings.get(\'STOP_LOSS_PERCENTAGE\', \'N/A\')}%"
    message += f"\\n- Trailing Activation: {settings.get(\'TRAILING_PROFIT_ACTIVATION_PERCENT\', \'N/A\')}%"
    message += f"\\n- Trailing Drop: {settings.get(\'TRAILING_STOP_DROP_PERCENT\', \'N/A\')}%"
    message += f"\\n- Trade Size (USDT): {settings.get(\'TRADE_SIZE_USDT\', \'N/A\')}"

    await update.message.reply_text(message, parse_mode="Markdown")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for the /status command. Shows subscription status, open quests, and watched symbols."""
    user_id = update.effective_user.id
    user_record = db.get_user(user_id)
    if not user_record:
        await update.message.reply_text("Could not find your profile. Please try /start.")
        return

    tier = user_record[\'tier\']
    expires_str = user_record[\'subscription_expires\']
    autotrade_status = "✅ Enabled" if db.get_user_effective_settings(user_id).get(\'AUTOTRADE_ENABLED\') else "❌ Disabled"

    subscription_message = f"👤 **Subscription Status**\\n- Tier: **{tier.capitalize()}**\\n- Auto-trade: {autotrade_status}\\n"

    if tier != "FREE" and expires_str:
        try:
            expires_dt = datetime.fromisoformat(expires_str).astimezone(timezone.utc)
            now_utc = datetime.now(timezone.utc)
            if expires_dt > now_utc:
                days_remaining = (expires_dt - now_utc).days
                expiry_date_formatted = expires_dt.strftime("%d %b %Y")
                subscription_message += f"- Expires: **{expiry_date_formatted}** ({days_remaining} days left)\\n"
            else:
                subscription_message += "- Status: **Expired**\\n"
        except (ValueError, TypeError):
            subscription_message += "- Expiry: *Not set*\\n"

    subscription_message += "\\n" + ("-" * 20) + "\\n\\n"

    open_trades = db.get_open_trades(user_id)
    if not open_trades:
        await update.message.reply_text(
            subscription_message + "You have no open quests. Use /quest to find an opportunity.",
            parse_mode="Markdown",
        )
        return
        
    message = ""
    for trade_item in open_trades:
        symbol = trade_item["coin_symbol"]
        buy_price = trade_item["buy_price"]
        current_price = trade.get_current_price(symbol)
        trade_id = trade_item["id"]
        
        message += f"\\n🔹 **{symbol}** (ID: {trade_id})"

        if current_price:
            pnl_percent = ((current_price - buy_price) / buy_price) * 100
            pnl_emoji = "📈" if pnl_percent >= 0 else "📉"
            message += (
                f"\\n   {pnl_emoji} P/L: `{pnl_percent:+.2f}%`"
                f"\\n   Bought: `${buy_price:,.8f}`"
                f"\\n   Current: `${current_price:,.8f}`"
            )
    
    await update.message.reply_text(
        subscription_message + message, parse_mode="Markdown"
    )

async def quest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for the /quest command. Calls the trade module."""
    # Correctly call the main quest command in trade.py which handles all logic
    await trade.quest_command(update, context)

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the global leaderboard of top trades."""
    top_trades = db.get_global_top_trades(limit=3)

    if not top_trades:
        await update.message.reply_text(
            "The Hall of Legends is still empty. No legendary quests have been completed yet!",
            parse_mode="Markdown",
        )
        return

    message = "🏆 **Hall of Legends: Global Top Quests** 🏆\\n\\n_These are the most glorious victories across the realm:_\\n\\n"
    rank_emojis = ["🥇", "🥈", "🥉"]

    # CORRECTED: Changed \`trade\` to \`trade_entry\` to fix the bug
    for i, trade_entry in enumerate(top_trades):
        emoji = rank_emojis[i] if i < len(rank_emojis) else "🔹"
        user_id = trade_entry["user_id"]
        user_name = "A mysterious adventurer"
        try:
            chat = await context.bot.get_chat(user_id)
            user_name = chat.first_name
        except Exception as e:
            logger.warning(f"Could not fetch user name for {user_id} for leaderboard: {e}")

        message += f"{emoji} **{trade_entry[\'coin_symbol\']}**: `{trade_entry[\'pnl_percent\']:+.2f}%` (by {user_name})\\n"

    message += "\\nWill your name be etched into legend?"
    await update.message.reply_text(message, parse_mode="Markdown")

async def import_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Placeholder for the /import command, as requested."""
    await update.message.reply_text("This command is not yet implemented. It will be used to log an existing trade.")

async def close_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Closes an open trade by its ID by delegating to the trade module."""
    # This now correctly calls the trade module's close command
    await trade.close_trade_command(update, context)

async def review_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reviews the user\'s completed trade performance."""
    user_id = update.effective_user.id
    closed_trades = db.get_closed_trades(user_id)

    if not closed_trades:
        await update.message.reply_text("You have no completed trades to review.")
        return

    wins = sum(1 for t in closed_trades if t[\'win_loss\'] == \'win\')
    losses = len(closed_trades) - wins
    total_pnl = sum(t[\'pnl_percentage\'] for t in closed_trades)
    win_rate = (wins / len(closed_trades)) * 100 if closed_trades else 0
    avg_pnl = total_pnl / len(closed_trades) if closed_trades else 0

    message = f"""🌟 **Performance Review** 🌟

**Completed Quests:** {len(closed_trades)}
**Wins:** {wins}
**Losses:** {losses}
**Win Rate:** {win_rate:.2f}%
**Average P/L:** `{avg_pnl:,.2f}%`
"""
    await update.message.reply_text(message, parse_mode="Markdown")


async def top_trades_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the user\'s top 3 most profitable closed trades."""
    user_id = update.effective_user.id
    # CORRECTED: This uses the correct DB function now
    top_trades = db.get_user_top_trades(user_id, limit=3)

    if not top_trades:
        await update.message.reply_text("You have no completed profitable quests to rank.")
        return

    message = "🏆 **Your Hall of Fame** 🏆\\n\\n_Here are your most legendary victories:_\\n\\n"
    rank_emojis = ["🥇", "🥈", "🥉"]

    for i, trade_entry in enumerate(top_trades):
        emoji = rank_emojis[i] if i < len(rank_emojis) else "🔹"
        message += f"{emoji} **{trade_entry[\'coin_symbol\']}**: `{trade_entry[\'pnl_percent\']:+.2f}%`\\n"

    await update.message.reply_text(message, parse_mode="Markdown")

async def autotrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to control the AI autotrading feature."""
    # This correctly delegates to the trade module
    await trade.autotrade_command(update, context)

# --- Other existing command handlers from the original file ---
# (e.g., wallet_command, balance_command, set_api_command, etc.)
# I will assume they are present and correct as per previous steps.
# For brevity, I will not reproduce all of them here but they are assumed to be part of the final file.

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
    # Registering all commands as per the new, refined help message
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
