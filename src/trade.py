
"""
This module handles the Telegram bot commands and acts as the interface
between the user and the core trading logic.

It is responsible for:
- Handling user commands (e.g., /myprofile, /help, /about, /clear_redis).
- Calling the appropriate functions in the core logic modules.
- Formatting and sending responses to the user.
"""

import logging
import asyncio
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from binance.client import Client as BinanceClient
from binance.exceptions import BinanceAPIException


# CORRECTED: Using relative imports for containerized deployment
from .core import binance_client
from .core import trading_logic
from .core.binance_client import TradeError
from . import db as new_db
from .utils import redis_utils
from . import config
from . import slip_manager


logger = logging.getLogger(__name__)

# --- Binance Client Initialization ---
binance_client.ensure_binance_client()
client = binance_client.client
BINANCE_AVAILABLE = binance_client.BINANCE_AVAILABLE
BINANCE_INIT_ERROR = binance_client.BINANCE_INIT_ERROR
if not BINANCE_AVAILABLE:
    logger.error(f"Failed to initialize Binance client: {BINANCE_INIT_ERROR}")

HELP_MESSAGE = """ðŸ¤– *Lunessa Shai'ra Gork* (@Srskat_bot) â€“ Automated Crypto Trading by LunessaSignals

*Core Commands:*
/help â€“ Show this help message
/myprofile â€“ View your open trades, balances, and settings
/setapi `KEY SECRET` â€“ Securely add your Binance API keys (in private chat)
/close `ID` â€“ Manually close an open trade by its ID
/addcoins `SYMBOL1 SYMBOL2...` - Add coins to your watchlist

*Utility Commands:*
/clear_redis â€“ Clear the bot's cache (for debugging)
/about â€“ Learn more about the LunessaSignals project

*How to Trade:*
1. Use `/setapi` in a private message with me to add your keys.
2. The bot will automatically start trading for you based on your settings.
3. Use `/myprofile` to monitor your performance.
4. If you sell a coin manually on Binance, use `/close` to update the bot.
"""

ABOUT_MESSAGE = (
    "*About Lunessa Shai'ra Gork* (@Srskat_bot)\n\n"
    "LunessaSignals is your AI-powered crypto trading companion. She monitors markets, manages risk, and keeps you updated via Telegram.\n\n"
    "Project: https://github.com/Drknessheo/lunara-bot\n"
    "License: MIT\n"
)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_MESSAGE, parse_mode="Markdown")


async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(ABOUT_MESSAGE, parse_mode="Markdown")

async def wallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    mode, paper_balance = new_db.get_user_trading_mode_and_balance(user_id)
    message = f"ðŸ’° **Wallet ({mode} Mode)** ðŸ’°\n\n"

    if mode == "LIVE":
        try:
            wallet_balances = get_all_spot_balances(user_id)
            if wallet_balances:
                open_trades = new_db.get_open_trades_by_user(user_id)
                open_trade_symbols = {trade['symbol'].replace("USDT", "") for trade in open_trades}

                core_holdings_found = False
                for bal in wallet_balances:
                    asset = bal["asset"]
                    free = float(bal["free"])
                    locked = float(bal["locked"])
                    total = free + locked

                    if total > 0.00000001:
                        if asset in open_trade_symbols:
                            message += f"- **{asset}:** `{total:.4f}` (Open Trade)\n"
                        else:
                            message += f"- **{asset}:** `{total:.4f}` (Core Holding)\n"
                            core_holdings_found = True
                if not core_holdings_found and not open_trades:
                    message += "  No significant core holdings found.\n"
            else:
                message += "  No assets found in your spot wallet.\n"
        except TradeError as e:
            message += f"  *Could not retrieve wallet balances: {str(e)}*\n"
        except Exception as e:
            logger.error(f"Unexpected error fetching wallet balances: {e}")
            message += "  *An unexpected error occurred while fetching wallet balances.*\n"
    elif mode == "PAPER":
        message += f"**Paper Balance:** ${paper_balance:,.2f} USDT\n"

    await update.message.reply_text(message, parse_mode="Markdown")


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not context.args:
        # Display current settings
        settings = new_db.get_user_effective_settings(user_id)
        message = "**Your current settings:**\n"
        for key, value in settings.items():
            message += f"- `{key}`: `{value}`\n"
        message += "\nTo change a setting, use `/settings <setting_name> <value>`."
        await update.message.reply_text(message, parse_mode="Markdown")
        return

    try:
        setting_name = context.args[0].lower()
        value_str = context.args[1]
        
        # Validate setting name
        if setting_name not in new_db.SETTING_TO_COLUMN_MAP:
            await update.message.reply_text(f"Invalid setting: {setting_name}")
            return

        # Convert value to appropriate type
        value = float(value_str)
        
        new_db.update_user_setting(user_id, setting_name, value)
        await update.message.reply_text(f"Successfully updated {setting_name} to {value}.")

    except (IndexError, ValueError):
        await update.message.reply_text("Usage: `/settings <setting_name> <value>`")
    except Exception as e:
        logger.error(f"Error updating user settings: {e}")
        await update.message.reply_text("An error occurred while updating your settings.")


async def myprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /myprofile command, showing open trades, wallet holdings, and settings."""
    user_id = update.effective_user.id
    mode, paper_balance = new_db.get_user_trading_mode_and_balance(user_id)

    message = f"âœ¨ **Your Trading Profile ({mode} Mode)** âœ¨\n\n"

    # --- Display Open Trades ---
    open_trades = new_db.get_open_trades_by_user(user_id)
    if open_trades:
        message += "ðŸ“Š **Open Quests:**\n"
        for trade_item in open_trades:
            symbol = trade_item["symbol"]
            buy_price = trade_item["buy_price"]
            quantity = trade_item["quantity"]
            trade_id = trade_item["id"]

            # Attempt to get current price for P/L calculation
            current_price = get_current_price(symbol)
            pnl_text = ""
            if current_price:
                pnl_percent = ((current_price - buy_price) / buy_price) * 100
                pnl_text = f" (P/L: `{pnl_percent:+.2f}%`)"

            message += (
                f"- **{symbol}** (ID: {trade_id})\n"
                f"  - Bought: `${buy_price:,.8f}`\n"
                f"  - Qty: `{quantity:.4f}`{pnl_text}\n"
            )
        message += "\n"
    else:
        message += "ðŸ“Š **Open Quests:** None\n\n"

    # --- Display Wallet Holdings (Live Mode Only) ---
    if mode == "LIVE":
        message += "ðŸ’° **Wallet Holdings:**\n"
        try:
            wallet_balances = get_all_spot_balances(user_id)
            if wallet_balances:
                # Get symbols from open trades for differentiation
                open_trade_symbols = {trade_item["symbol"].replace("USDT", "") for trade_item in open_trades}

                core_holdings_found = False
                for bal in wallet_balances:
                    asset = bal["asset"]
                    free = float(bal["free"])
                    locked = float(bal["locked"])
                    total = free + locked

                    # Only show assets with a significant balance
                    if total > 0.00000001:
                        # Check if this asset is part of an open trade
                        if asset in open_trade_symbols:
                            message += f"- **{asset}:** `{total:.4f}` (Open Trade)\n"
                        else:
                            message += f"- **{asset}:** `{total:.4f}` (Core Holding)\n"
                            core_holdings_found = True
                if not core_holdings_found and not open_trades:
                    message += "  No significant core holdings found.\n"
            else:
                message += "  No assets found in your spot wallet.\n"
        except TradeError as e:
            # TradeError should contain a readable string; use str(e)
            message += f"  *Could not retrieve wallet balances: {str(e)}*\n"
        except Exception as e:
            logger.error(f"Unexpected error fetching wallet balances for status: {e}")
            message += (
                "  *An unexpected error occurred while fetching wallet balances.*\n"
            )
    elif mode == "PAPER":
        message += f"ðŸ’° **Paper Balance:** ${paper_balance:,.2f} USDT\n"

    # --- Display Autotrade Settings ---
    settings = new_db.get_user_effective_settings(user_id)
    message += "\nâš™ï¸  **Autotrade Settings:**\n"
    for key, value in settings.items():
        message += f"- `{key}`: `{value}`\n"

    await update.message.reply_text(message, parse_mode="Markdown")

async def clear_redis_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /clear_redis command."""
    success, message = redis_utils.clear_redis_cache()
    if success:
        await update.message.reply_text(f"âœ… {message}")
    else:
        await update.message.reply_text(f"âš ï¸  {message}")


async def set_api_keys_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Securely store a user's Binance API key and secret."""
    if update.effective_chat.type != "private":
        await update.message.reply_text("For your safety, please send API keys in a private chat with the bot.")
        return

    user_id = update.effective_user.id
    try:
        api_key = context.args[0].strip()
        secret_key = context.args[1].strip()
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /setapi <KEY> <SECRET>\nSend this command in a private chat with the bot.")
        return

    try:
        # Use the db helper to encrypt and store keys
        new_db.store_user_api_keys(user_id, api_key, secret_key)
        await update.message.reply_text(
            "âœ… Your API keys have been stored securely. Live trading features are now available to you.",
            parse_mode=ParseMode.MARKDOWN,
        )
        logger.info(f"Successfully stored API keys for user {user_id}.")
        # Test keys by fetching balance
        await update.message.reply_text("Attempting to verify keys by fetching wallet balance...")
        try:
            balances = get_all_spot_balances(user_id)
            if balances is not None:
                await update.message.reply_text("âœ… API keys verified successfully!")
            else:
                await update.message.reply_text("âš ï¸  Verification failed. Could not fetch balances. Please check your keys and permissions.")
        except TradeError as e:
            await update.message.reply_text(f"âš ï¸  Verification failed: {e}")

    except Exception as e:
        logger.exception("Failed to store API keys for user %s: %s", user_id, e)
        await update.message.reply_text("An error occurred while saving your API keys. Please contact the administrator.")


async def close_trade_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Closes an open trade by its ID, closing it in the DB and removing the Redis slip."""
    user_id = update.effective_user.id
    try:
        trade_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text(
            "Please provide a valid trade ID.\nUsage: `/close <trade_id>`",
            parse_mode="Markdown",
        )
        return

    trade_to_close = new_db.find_open_trade(trade_id, user_id)

    if not trade_to_close:
        await update.message.reply_text(
            "Could not find an open trade with that ID under your name. Check `/myprofile`.",
            parse_mode="Markdown",
        )
        return

    symbol = trade_to_close["symbol"]
    buy_price = trade_to_close["buy_price"]
    
    # Use a placeholder price for closing manually, as the actual sell happened on Binance
    # and the bot doesn't know the price.
    manual_close_price = get_current_price(symbol) or buy_price
    pnl_percentage = ((manual_close_price - buy_price) / buy_price) * 100
    win_loss = "win" if pnl_percentage > 0 else "loss" if pnl_percentage < 0 else "breakeven"
    
    new_db.mark_trade_closed(trade_id, reason="manual_close")


    # Now, attempt to clean up the corresponding Redis slip
    try:
        # This is a necessary workaround because the DB trade ID is not stored in the slip.
        # We must find the slip by the symbol. This is risky if multiple trades for the
        # same symbol are open, but the bot logic tries to prevent this.
        slip_key_found = None
        active_slips = slip_manager.list_all_slips()
        for slip in active_slips:
            data = slip.get("data", {})
            # Match the symbol to find the right slip to clean.
            if data.get("symbol") == symbol:
                slip_key_found = slip["key"]
                break
        
        if slip_key_found:
            slip_manager.cleanup_slip(slip_key_found)
            message = (
                f"âœ… Trade #{trade_id} ({symbol}) has been manually closed.\n"
                f"The monitoring slip for **{symbol}** has also been removed."
            )
            await update.message.reply_text(message, parse_mode="Markdown")
        else:
            message = (
                f"âœ… Trade #{trade_id} ({symbol}) was closed in the database, "
                f"but no active monitoring slip was found for that symbol to remove."
            )
            await update.message.reply_text(message, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error during slip cleanup for trade {trade_id}: {e}")
        await update.message.reply_text(
            f"Trade #{trade_id} was closed, but an error occurred during Redis slip cleanup. Please report this."
        )

async def binance_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Checks and reports the status of the connection to Binance."""
    # Use the global flag and error message for a more informative response
    if not BINANCE_AVAILABLE:
        await update.message.reply_text(f"âŒ Binance client not initialized. Error: {BINANCE_INIT_ERROR}")
        return
    try:
        # The client is initialized in the global scope of the module.
        # ping() is a synchronous call, but it's fast.
        client.ping()
        await update.message.reply_text("âœ… Binance API is reachable.")
    except Exception as e:
        logger.error(f"Binance API ping failed: {e}")
        await update.message.reply_text(f"âŒ Binance API error: {e}")

async def usercount_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the total number of users."""
    user_count = new_db.get_user_count()
    await update.message.reply_text(f"Total users: {user_count}")

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the user's balance."""
    user_id = update.effective_user.id
    mode, paper_balance = new_db.get_user_trading_mode_and_balance(user_id)
    if mode == "PAPER":
        await update.message.reply_text(f"Your paper balance is: ${paper_balance:,.2f} USDT")
    else:
        try:
            balances = get_all_spot_balances(user_id)
            usdt_balance = next((item for item in balances if item["asset"] == "USDT"), None)
            if usdt_balance:
                await update.message.reply_text(f"Your live USDT balance is: {usdt_balance['free']}")
            else:
                await update.message.reply_text("Could not retrieve your USDT balance.")
        except Exception as e:
            await update.message.reply_text(f"Could not retrieve your balance: {e}")

async def scheduled_monitoring_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodically monitors the market and triggers trades."""
    logger.info("Running scheduled monitoring job...")
    # This is a placeholder for the a user_id = update.effective_user.id
    try:
        current_mode, _ = new_db.get_user_trading_mode_and_balance(user_id)
        new_mode = 'LIVE' if current_mode == 'PAPER' else 'PAPER'
        new_db.set_user_trading_mode(user_id, new_mode)

        if new_mode == 'PAPER':
            message = "Paper trading mode has been enabled. Your trades will be simulated and will not use real funds."
        else:
            message = "Live trading mode has been activated. The bot will now execute trades with real funds."

        await update.message.reply_text(f"✅ {message}")

    except Exception as e:
        logger.error(f"Failed to toggle paper trading for user {user_id}: {e}")
        await update.message.reply_text("An error occurred while switching trading modes. Please try again later.")

async def autotrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Placeholder for autotrade command."""
    await update.message.reply_text("Autotrade command is not yet implemented.")
