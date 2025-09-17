
"""
This module handles the Telegram bot commands and acts as the interface
between the user and the core trading logic.
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from binance.client import Client as BinanceClient
from binance.exceptions import BinanceAPIException

from .core import binance_client
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

# --- Bot Command Handlers ---

HELP_MESSAGE = """ðŸ¤– *Lunessa Shai'ra Gork* (@Srskat_bot) - Your AI Trading Companion

*Core Commands:*
/myprofile - View your trades, balances, and settings.
/settings `<name>` `<value>` - Change a setting (e.g., `/settings autotrade on`).
/setapi `<KEY>` `<SECRET>` - Securely add Binance keys (in private chat).
/close `<ID>` - Manually close an open trade.
/addcoins `<SYMBOL1>` `...` - Add coins to your watchlist.

*Utility Commands:*
/help - Show this help message.
/about - Learn about the project.
"""

ABOUT_MESSAGE = (
    "*About Lunessa Shai'ra Gork* (@Srskat_bot)\n\n"
    "An AI-powered crypto trading companion from the LunessaSignals project."
    "\nProject: https://github.com/Drknessheo/lunara-bot"
)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_MESSAGE, parse_mode=ParseMode.MARKDOWN)

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(ABOUT_MESSAGE, parse_mode=ParseMode.MARKDOWN)

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not context.args:
        settings = new_db.get_user_effective_settings(user_id)
        message = "**Your current settings:**\n"
        for key, value in settings.items():
            message += f"- `{key}`: `{value}`\n"
        message += "\nTo change a setting, use `/settings <setting_name> <value>`."
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
        return

    try:
        setting_name = context.args[0].lower()
        value_str = " ".join(context.args[1:])
        
        new_db.update_user_setting(user_id, setting_name, value_str)
        updated_settings = new_db.get_user_effective_settings(user_id)
        new_value = updated_settings.get(setting_name, value_str)

        await update.message.reply_text(f"âœ… Successfully updated `{setting_name}` to `{new_value}`.", parse_mode=ParseMode.MARKDOWN)

    except (IndexError, ValueError):
        await update.message.reply_text("Usage: `/settings <setting_name> <value>`")
    except (TypeError, ValueError) as e:
        await update.message.reply_text(f"âŒ Invalid value for `{setting_name}`: {e}")
    except Exception as e:
        logger.error(f"Error updating user settings: {e}")
        await update.message.reply_text("An error occurred while updating your settings.")

async def myprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    mode, paper_balance = new_db.get_user_trading_mode_and_balance(user_id)
    message = f"âœ¨ **Your Trading Profile ({mode} Mode)** âœ¨\n\n"

    open_trades = new_db.get_open_trades_by_user(user_id)
    if open_trades:
        message += "ðŸ“Š **Open Trades:**\n"
        for trade in open_trades:
            pnl_text = ""
            current_price = get_current_price(trade['symbol'])
            if current_price:
                pnl_percent = ((current_price - trade['buy_price']) / trade['buy_price']) * 100
                pnl_text = f" (P/L: `{pnl_percent:+.2f}%`)"
            message += f"- **{trade['symbol']}** (ID: {trade['id']}){pnl_text}\n"
    else:
        message += "ðŸ“Š **Open Trades:** None\n"

    if mode == "LIVE":
        message += "\nðŸ’° **Wallet Holdings:**\n"
        try:
            balances = get_all_spot_balances(user_id)
            if balances:
                for bal in balances:
                    message += f"- **{bal['asset']}:** `{float(bal['free']):.4f}`\n"
            else:
                message += "  No assets found.\n"
        except TradeError as e:
            message += f"  *Could not retrieve balances: {e}*\n"
    else:
        message += f"\nðŸ’° **Paper Balance:** ${paper_balance:,.2f} USDT\n"
    
    settings = new_db.get_user_effective_settings(user_id)
    message += "\nâš™ï¸  **Autotrade Settings:**\n"
    for key, value in settings.items():
        message += f"- `{key}`: `{value}`\n"

    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

async def set_api_keys_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Please send API keys in a private chat.")
        return

    user_id = update.effective_user.id
    try:
        api_key, secret_key = context.args[0], context.args[1]
        new_db.store_user_api_keys(user_id, api_key, secret_key)
        await update.message.reply_text("âœ… API keys stored. Verifying...")
        try:
            balances = get_all_spot_balances(user_id)
            await update.message.reply_text("âœ… API keys verified successfully!")
        except TradeError as e:
            await update.message.reply_text(f"âš ï¸  Verification failed: {e}")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /setapi <KEY> <SECRET>")
    except Exception as e:
        await update.message.reply_text(f"An error occurred: {e}")

async def close_trade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        trade_id = int(context.args[0])
        trade = new_db.find_open_trade(trade_id, user_id)
        if not trade:
            await update.message.reply_text("Trade not found.")
            return
        new_db.mark_trade_closed(trade_id)
        slip_manager.cleanup_slip_for_symbol(trade['symbol'])
        await update.message.reply_text(f"âœ… Trade #{trade_id} ({trade['symbol']}) manually closed.")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: `/close <trade_id>`")

async def quest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /quest command, ensuring symbol is formatted correctly."""
    if not context.args:
        await update.message.reply_text("Usage: `/quest <SYMBOL>`")
        return

    symbol = context.args[0].upper()
    if not symbol.endswith('USDT'):
        symbol += 'USDT'
    
    price = get_current_price(symbol)
    if price is not None:
        await update.message.reply_text(f"The current price of {symbol} is ${price:,.2f}.")
    else:
        await update.message.reply_text(f"Could not get price for {symbol}.")

async def addcoins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: `/addcoins <SYMBOL1> <SYMBOL2>...`")
        return
    
    new_db.add_coins_to_watchlist(user_id, [coin.upper() for coin in context.args])
    await update.message.reply_text(f"Successfully added coins to your watchlist.")

# --- Helper Functions ---

def get_current_price(symbol: str) -> float | None:
    if not client:
        return None
    try:
        ticker = client.get_ticker(symbol=symbol)
        return float(ticker['lastPrice'])
    except BinanceAPIException as e:
        if e.code == -1121: # Invalid symbol
            logger.warning(f"Invalid symbol for price check: {symbol}")
        else:
            logger.error(f"Binance error getting price for {symbol}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error getting current price for {symbol}: {e}")
        return None

def get_all_spot_balances(user_id: int) -> list | None:
    api_key, secret_key = new_db.get_user_api_keys(user_id)
    if not api_key or not secret_key:
        raise TradeError("API keys not set. Use /setapi.")
    try:
        user_client = BinanceClient(api_key, secret_key)
        account_info = user_client.get_account()
        return [bal for bal in account_info["balances"] if float(bal["free"]) > 0 or float(bal["locked"]) > 0]
    except BinanceAPIException as e:
        raise TradeError(f"Binance API error: {e.message}")
    except Exception as e:
        raise TradeError(f"Unexpected error fetching balances: {e}")
