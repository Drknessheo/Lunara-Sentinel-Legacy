
"""
This module handles the Telegram bot commands and acts as the interface
between the user and the core trading logic.
"""

import logging
import json
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

# Local Imports
from .core import binance_client
from .core.binance_client import TradeError
from . import db as new_db
from . import config
from . import slip_manager
from . import autotrade_settings

logger = logging.getLogger(__name__)

# --- Bot Command Handlers ---

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not context.args:
        settings = await autotrade_settings.get_effective_settings(user_id)
        message = "<b>Your current settings:</b>\n"
        for key, value in settings.items():
            message += f"- <code>{key}</code>: <code>{value}</code>\n"
        message += "\nTo change a setting, use <code>/settings &lt;setting_name&gt; &lt;value&gt;</code>."
        await update.message.reply_html(message)
        return

    try:
        setting_name = context.args[0].lower()
        value_str = " ".join(context.args[1:])
        
        success, message = await autotrade_settings.validate_and_set(user_id, setting_name, value_str)

        if success:
            await update.message.reply_html(f"✅ {message}")
        else:
            await update.message.reply_html(f"❌ {message}")

    except (IndexError, ValueError):
        await update.message.reply_text("Usage: <code>/settings &lt;setting_name&gt; &lt;value&gt;</code>")
    except Exception as e:
        logger.error(f"Error updating user settings: {e}")
        await update.message.reply_text("An error occurred while updating your settings.")

async def myprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    mode, paper_balance = new_db.get_user_trading_mode_and_balance(user_id)
    message = f"✨ <b>Your Trading Profile ({mode} Mode)</b> ✨\n\n"

    open_trades = new_db.get_open_trades_by_user(user_id)
    if open_trades:
        message += "📊 <b>Open Trades:</b>\n"
        for trade in open_trades:
            pnl_text = ""
            current_price = await binance_client.get_current_price(trade['symbol'])
            if current_price:
                pnl_percent = ((current_price - trade['buy_price']) / trade['buy_price']) * 100
                pnl_text = f" (P/L: <code>{pnl_percent:+.2f}%</code>)"
            message += f"- <b>{trade['symbol']}</b> (ID: {trade['id']}){pnl_text}\n"
    else:
        message += "📊 <b>Open Trades:</b> None\n"

    if mode == "LIVE":
        message += "\n💰 <b>Wallet Holdings:</b>\n"
        try:
            balances = await binance_client.get_all_spot_balances(user_id)
            if balances:
                for bal in balances:
                    message += f"- <b>{bal['asset']}:</b> <code>{float(bal['free']):.4f}</code>\n"
            else:
                message += "  No assets found.\n"
        except TradeError as e:
            message += f"  <i>Could not retrieve balances: {e}</i>\n"
    else:
        message += f"\n💰 <b>Paper Balance:</b> ${paper_balance:,.2f} USDT\n"
    
    settings = await autotrade_settings.get_effective_settings(user_id)
    message += "\n⚙️ <b>Autotrade Settings:</b>\n"
    for key, value in settings.items():
        message += f"- <code>{key}</code>: <code>{value}</code>\n"

    await update.message.reply_html(message)

async def set_api_keys_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Please send API keys in a private chat.")
        return

    user_id = update.effective_user.id
    try:
        api_key, secret_key = context.args[0], context.args[1]
        new_db.store_user_api_keys(user_id, api_key, secret_key)
        await update.message.reply_text("✅ API keys stored. Verifying...")
        try:
            balances = await binance_client.get_all_spot_balances(user_id)
            await update.message.reply_text("✅ API keys verified successfully!")
        except TradeError as e:
            await update.message.reply_html(f"⚠️ Verification failed: {e}")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /setapi <KEY> <SECRET>")
    except Exception as e:
        await update.message.reply_text(f"An error occurred: {e}")

async def close_trade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        trade_id = int(context.args[0])
        trade = new_db.find_open_trade_by_id(trade_id, user_id)
        if not trade:
            await update.message.reply_text("Trade not found.")
            return
        new_db.mark_trade_closed(trade_id)
        slip_manager.cleanup_slip_for_symbol(trade['symbol'])
        await update.message.reply_html(f"✅ Trade #{trade_id} ({trade['symbol']}) manually closed.")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: <code>/close &lt;trade_id&gt;</code>")

async def quest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: <code>/quest &lt;SYMBOL&gt;</code>")
        return

    symbol = context.args[0].upper()
    if not symbol.endswith('USDT'):
        symbol += 'USDT'
    
    price = await binance_client.get_current_price(symbol)
    if price is not None:
        await update.message.reply_html(f"The current price of {symbol} is ${price:,.2f}.")
    else:
        await update.message.reply_html(f"Could not get price for {symbol}.")

async def addcoins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: <code>/addcoins &lt;SYMBOL1&gt; &lt;SYMBOL2&gt;...</code>")
        return
    
    new_db.add_coins_to_watchlist(user_id, [coin.upper() for coin in context.args])
    await update.message.reply_text(f"Successfully added coins to your watchlist.")

async def removecoins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: <code>/removecoins &lt;SYMBOL1&gt; &lt;SYMBOL2&gt;...</code>")
        return
    
    new_db.remove_coins_from_watchlist(user_id, [coin.upper() for coin in context.args])
    await update.message.reply_text(f"Successfully removed coins from your watchlist.")

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    mode, paper_balance = new_db.get_user_trading_mode_and_balance(user_id)

    if mode == 'LIVE':
        try:
            balances = await binance_client.get_all_spot_balances(user_id)
            usdt_balance = next((item for item in balances if item["asset"] == "USDT"), None)
            balance_str = f"{float(usdt_balance['free']):.2f} USDT" if usdt_balance else "Not found"
            message = f"💰 Your LIVE USDT balance: <code>{balance_str}</code>"
        except TradeError as e:
            message = f"Could not retrieve balances: {e}"
    else:
        message = f"💰 Your PAPER balance: ${paper_balance:,.2f} USDT"
    
    await update.message.reply_html(message)

async def diagnose_slip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Diagnoses a trade slip by retrieving its raw encrypted data from Redis."""
    user_id = update.effective_user.id
    if user_id != config.ADMIN_USER_ID:
        await update.message.reply_text("You are not authorized for this command.")
        return

    if not context.args:
        await update.message.reply_html("Usage: <code>/diagnose_slip &lt;trade_id&gt;</code>")
        return

    trade_id = context.args[0]
    redis_key = f"trade:{trade_id}:data"

    try:
        redis_client = slip_manager.get_redis_client()
        if not redis_client:
            await update.message.reply_text("Redis client is not available.")
            return

        raw_data = redis_client.get(redis_key)

        if not raw_data:
            await update.message.reply_text(f"No data found for Redis key: <code>{redis_key}</code>")
            return

        if isinstance(raw_data, bytes):
            raw_data_str = raw_data.decode('utf-8', 'ignore')
        else:
            raw_data_str = str(raw_data)

        decryption_result = ""
        try:
            decrypted_slip = slip_manager.get_and_decrypt_slip(trade_id)
            decryption_result = f"<b>Decryption Attempt:</b>\n<pre>{json.dumps(decrypted_slip, indent=2)}</pre>"
        except Exception as e:
            decryption_result = f"<b>Decryption Failed:</b>\n<pre>{e}</pre>"

        message = (
            f"<b>Diagnosis for Trade ID:</b> <code>{trade_id}</code>\n"
            f"<b>Redis Key:</b> <code>{redis_key}</code>\n\n"
            f"<b>Raw Encrypted Data:</b>\n<pre>{raw_data_str}</pre>\n\n"
            f"{decryption_result}"
        )

        await update.message.reply_html(message)

    except Exception as e:
        logger.error(f"Error diagnosing slip {trade_id}: {e}", exc_info=True)
        await update.message.reply_text(f"An error occurred: {e}")

