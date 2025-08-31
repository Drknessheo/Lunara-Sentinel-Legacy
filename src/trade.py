"""Minimal trade helpers used by tests.

This file holds a small, well-defined surface used by tests:
- TradeError: exception type
- get_rsi(symbol): returns a float or None

Replace with the full implementation when available.
"""

# typing.Optional was unused; remove to satisfy linter


class TradeError(Exception):
    """Generic trade-related error used by tests."""


import logging

logger = logging.getLogger("tradebot")
logger.setLevel(logging.INFO)
if not logger.hasHandlers():
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)


# --- TradeError Exception ---
# Duplicate TradeError removed; top-level TradeError is the canonical exception for tests.


from telegram import Update
from telegram.ext import ContextTypes

HELP_MESSAGE = """🤖 *Lunessa Shai'ra Gork* (@Srskat_bot) – Automated Crypto Trading by LunessaSignals

*Features:*
- Rule-driven signals (RSI, MACD, Bollinger Bands)
- Risk controls: stop-loss, trailing stop, allocation
- LIVE/TEST modes
- Telegram alerts and remote control

*Main Commands:*
/help – Show usage and features
/status – Show wallet and trade status
/import – Import trades manually
/about – Learn more about LunessaSignals

*Supported Coins:*
BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, ADAUSDT, XRPUSDT, DOGEUSDT, MATICUSDT, DOTUSDT, AVAXUSDT, LINKUSDT, ARBUSDT, OPUSDT, LTCUSDT, TRXUSDT, SHIBUSDT, PEPEUSDT, UNIUSDT, SUIUSDT, INJUSDT, RNDRUSDT, PENGUUSDT, CTKUSDT, OMBTC, ENAUSDT, HYPERUSDT, BABYUSDT, KAITOUSDT

*Get started:* Add your Binance API keys and Telegram bot token, then run the bot!"""

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


import asyncio
import json
import logging
import math
import os
import re
import statistics
import time
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np
import pandas as pd
import redis
from binance.client import Client
from binance.exceptions import BinanceAPIException
from telegram import Update
from telegram.ext import ContextTypes

import config
import config as _config
import gemini_cacher
from indicators import calc_atr, calculate_rsi
from memory import log_trade_outcome
from modules import db_access as db

# adaptive_strategy_job implemented locally below; avoid importing the external symbol to prevent redefinition
from risk_management import get_atr_stop, should_pause_trading, update_daily_pl
from trade_guard import TradeValidator

# --- Market Crash/Big Buyer Shield ---
# Now imported from risk_management.py


@lru_cache(maxsize=128)
def get_symbol_info(symbol: str):
    """
    Fetches and caches trading rules for a symbol, like precision.
    Returns a dictionary with symbol information or None on error.
    """
    try:
        if not client:
            return None
        return client.get_symbol_info(symbol)
    except BinanceAPIException as e:
        logger.error("Could not fetch symbol info for %s: %s", symbol, e)
        return None
    except Exception as e:
        logger.error("Unexpected error fetching symbol info for %s: %s", symbol, e)
        return None


# Initialize Binance client with defensive handling.
# BINANCE_AVAILABLE will be True if the client was successfully created.
BINANCE_AVAILABLE = False
BINANCE_INIT_ERROR = None
client = None
if config.BINANCE_API_KEY and config.BINANCE_SECRET_KEY:
    try:
        client = Client(config.BINANCE_API_KEY, config.BINANCE_SECRET_KEY)
        BINANCE_AVAILABLE = True
        logger.info("Binance client initialized successfully.")
    except BinanceAPIException as e:
        BINANCE_INIT_ERROR = repr(e)
        # Common case in CI or restricted runners: HTTP 451 restricted location.
        if "451" in repr(e) or "restricted location" in str(e).lower():
            logger.warning(
                "Binance API unavailable due to restricted location (451). Trading disabled."
            )
        else:
            logger.exception("Failed to initialize Binance client; trading disabled.")
        client = None
    except Exception as e:
        BINANCE_INIT_ERROR = repr(e)
        logger.exception(
            "Unexpected error initializing Binance client; trading disabled."
        )
        client = None
else:
    logger.warning("Binance API keys not found. Trading functions will be disabled.")
    client = None


def get_user_client(user_id: int):
    """Creates a Binance client instance for a specific user using their stored keys."""
    # For the admin user, prioritize API keys from config.py (loaded from .env)
    if user_id == config.ADMIN_USER_ID:
        if config.BINANCE_API_KEY and config.BINANCE_SECRET_KEY:
            try:
                return Client(config.BINANCE_API_KEY, config.BINANCE_SECRET_KEY)
            except Exception as e:
                logger.error(
                    f"Failed to create Binance client for ADMIN_USER_ID from config: {e}"
                )
                return None
        else:
            logger.warning(
                "ADMIN_USER_ID detected, but BINANCE_API_KEY or BINANCE_SECRET_KEY not found in config."
            )
            return None

    # For other users, fetch API keys from the database
    api_key, secret_key = db.get_user_api_keys(user_id)
    if not api_key or not secret_key:
        logger.warning(f"API keys not found for user {user_id}.")
        return None
    try:
        return Client(api_key, secret_key)
    except Exception as e:
        logger.error(f"Failed to create Binance client for user {user_id}: {e}")
        return None


def is_weekend():
    """Checks if the current day is Saturday or Sunday (UTC)."""
    # weekday() returns 5 for Saturday, 6 for Sunday
    return datetime.now(timezone.utc).weekday() >= 5


def get_current_price(symbol: str):
    """Fetches the current price of a given symbol from Binance."""
    try:
        ticker = client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])
    except BinanceAPIException as e:
        logger.error(f"Binance API error getting price for {symbol}: {e}")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred getting price for {symbol}: {e}")
        return None


def get_monitored_coins():
    return config.AI_MONITOR_COINS


def get_rsi(symbol="BTCUSDT", interval=Client.KLINE_INTERVAL_1HOUR, period=14):
    """Calculates the Relative Strength Index (RSI) for a given symbol."""
    try:
        # Fetch klines (candlestick data)
        klines = client.get_historical_klines(
            symbol, interval, f"{period + 100} hours ago UTC"
        )
        if len(klines) < period:
            return None  # Not enough data

        closes = np.array([float(k[4]) for k in klines])
        # Create a Series from the numpy array without copying data
        close_series = pd.Series(closes, copy=False)
        return calculate_rsi(close_series, period).iloc[-1]
    except BinanceAPIException as e:
        logger.error(f"Binance API error getting RSI for {symbol}: {e}")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred getting RSI for {symbol}: {e}")
        return None


def get_bollinger_bands(
    symbol, interval=Client.KLINE_INTERVAL_1HOUR, period=20, std_dev=2
):
    """Calculates Bollinger Bands for a given symbol."""
    try:
        # Fetch more klines to ensure SMA calculation is accurate
        klines = client.get_historical_klines(
            symbol, interval, f"{period + 50} hours ago UTC"
        )
        if len(klines) < period:
            return None, None, None, None

        closes = np.array([float(k[4]) for k in klines])

        # Calculate SMA and Standard Deviation for the most recent `period`
        sma = np.mean(closes[-period:])
        std = np.std(closes[-period:])

        upper_band = sma + (std * std_dev)
        lower_band = sma - (std * std_dev)
        return upper_band, sma, lower_band, std
    except Exception as e:
        logger.error(
            f"An unexpected error occurred getting Bollinger Bands for {symbol}: {e}"
        )
        return None, None, None, None


def get_macd(
    symbol,
    interval=Client.KLINE_INTERVAL_1HOUR,
    fast_period=12,
    slow_period=26,
    signal_period=9,
):
    """Calculates the MACD for a given symbol."""
    try:
        # Fetch enough klines for the slow EMA + signal line
        klines = client.get_historical_klines(
            symbol, interval, f"{slow_period + signal_period + 50} hours ago UTC"
        )
        if len(klines) < slow_period + signal_period:
            return None, None, None

        # ...existing code...
        closes = pd.Series([float(k[4]) for k in klines])

        # Calculate EMAs
        ema_fast = closes.ewm(span=fast_period, adjust=False).mean()
        ema_slow = closes.ewm(span=slow_period, adjust=False).mean()

        # Calculate MACD line
        macd_line = ema_fast - ema_slow

        # Calculate Signal line
        signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()

        # Calculate MACD Histogram
        macd_histogram = macd_line - signal_line

        # Return the most recent values
        return macd_line.iloc[-1], signal_line.iloc[-1], macd_histogram.iloc[-1]
    except Exception as e:
        logger.error(f"An unexpected error occurred getting MACD for {symbol}: {e}")
        return None, None, None


def get_account_balance(user_id: int, asset="USDT"):
    """Fetches the free balance for a specific asset from the Binance spot account."""
    user_client = get_user_client(user_id)
    if not user_client:
        return None
    try:
        balance = user_client.get_asset_balance(asset=asset)
        return float(balance["free"]) if balance else 0.0
    except BinanceAPIException as e:
        logger.error(
            f"Binance API error getting account balance for user {user_id}: {e}"
        )
        # Pass the specific error message up to the command handler
        raise TradeError(f"Binance API Error: {e.message}")
    except Exception as e:
        logger.error(
            f"An unexpected error occurred getting account balance for user {user_id}: {e}"
        )
        raise TradeError(f"An unexpected error occurred: {e}")


def get_last_trade_from_binance(user_id: int, symbol: str):
    """Fetches the user's most recent trade for a given symbol from Binance."""
    user_client = get_user_client(user_id)
    if not user_client:
        return None
    try:
        # Fetch the last trade. The list is ordered from oldest to newest.
        trades = user_client.get_my_trades(symbol=symbol, limit=1)  # type: ignore
        if not trades:
            return None
        return trades[0]  # The most recent trade
    except BinanceAPIException as e:
        logger.error(
            f"Binance API error getting last trade for {symbol} for user {user_id}: {e}"
        )
        return None
    except Exception as e:
        logger.error(
            f"An unexpected error occurred getting last trade for {symbol} for user {user_id}: {e}"
        )
        return None


def get_all_spot_balances(user_id: int) -> list[dict] | None:
    """Fetches all non-zero asset balances from the user's Binance spot account."""
    user_client = get_user_client(user_id)
    if not user_client:
        logger.warning(
            f"Cannot get spot balances for user {user_id}: client not available."
        )
        return None
    try:
        account_info = user_client.get_account()
        balances = [
            b
            for b in account_info.get("balances", [])
            if float(b["free"]) > 0 or float(b["locked"]) > 0
        ]
        return balances
    except BinanceAPIException as e:
        logger.error(f"Binance API error getting all balances for user {user_id}: {e}")
        raise TradeError(f"Binance API Error: {e.message}")
    except Exception as e:
        logger.error(f"Unexpected error getting all balances for user {user_id}: {e}")
        raise TradeError(f"An unexpected error occurred: {e}")


def place_buy_order(user_id: int, symbol: str, usdt_amount: float, is_test=False):
    """Places a live market buy order on Binance for a specific user."""
    user_client = get_user_client(user_id)
    if not user_client:
        logger.error(
            f"Cannot place buy order for user {user_id}: client not available."
        )
        raise TradeError("Binance client is not available. Please check your API keys.")

    # --- Get Symbol Trading Rules ---
    info = get_symbol_info(symbol)
    if not info:
        raise TradeError(f"Could not retrieve trading rules for {symbol}.")

    # --- Validate Order against Filters (minNotional, stepSize) ---
    min_notional = 0.0
    for f in info["filters"]:
        if f["filterType"] == "NOTIONAL":
            min_notional = float(f["minNotional"])
        if f["filterType"] == "LOT_SIZE":
            float(f["stepSize"])

    if usdt_amount < min_notional:
        raise TradeError(
            f"Order value of ${usdt_amount:.2f} is below the minimum of ${min_notional:.2f} for {symbol}."
        )

    try:
        logger.info(
            f"Attempting to BUY {usdt_amount} USDT of {symbol} for user {user_id}..."
        )
        # Use quoteOrderQty for market buys to specify the amount in USDT
        order = user_client.create_order(  # type: ignore
            symbol=symbol,
            side=Client.SIDE_BUY,
            type=Client.ORDER_TYPE_MARKET,
            quoteOrderQty=usdt_amount,
        )
        if is_test:
            # In a test, we can't get fill details, so we simulate them.
            # This part is for unit testing or dry runs if you expand on that.
            return (
                {"symbol": symbol, "orderId": "test_order"},
                get_current_price(symbol),
                usdt_amount / get_current_price(symbol),
            )

        logger.info(
            f"LIVE BUY order successful for {symbol} for user {user_id}: {order}"
        )

        # Extract details from the fill(s)
        entry_price = float(order["fills"][0]["price"])
        quantity = float(order["executedQty"])

        return order, entry_price, quantity

    except BinanceAPIException as e:
        logger.error(
            f"LIVE BUY order failed for {symbol} for user {user_id}: {e.message}"
        )
        raise TradeError(f"Binance API Error: {e.message}")
    except Exception as e:
        logger.error(
            f"An unexpected error occurred during LIVE BUY for {symbol} for user {user_id}: {e}"
        )
        raise TradeError(f"An unexpected error occurred: {e}")


def place_sell_order(user_id: int, symbol: str, quantity: float):
    """Places a live market sell order on Binance for a specific user."""
    user_client = get_user_client(user_id)
    if not user_client:
        logger.error(
            f"Cannot place SELL order for user {user_id}: client not available."
        )
        raise TradeError("Binance client is not available for selling.")

    info = get_symbol_info(symbol)
    if not info:
        raise TradeError(f"Could not retrieve trading rules for {symbol} to sell.")

    # Format quantity according to the symbol's stepSize filter
    step_size = float(
        [f["stepSize"] for f in info["filters"] if f["filterType"] == "LOT_SIZE"][0]
    )
    precision = int(round(-math.log(step_size, 10), 0))
    formatted_quantity = f"{quantity:.{precision}f}"

    try:
        # Notional check before placing order
        current_price = get_current_price(symbol)
        if not TradeValidator.is_trade_valid(
            symbol, float(formatted_quantity), current_price, user_id=user_id
        ):
            raise TradeError(
                f"Trade skipped: Notional value too low for {symbol} (user {user_id})"
            )
        logger.info(
            f"Attempting to SELL {formatted_quantity} of {symbol} for user {user_id}..."
        )
        order = user_client.order_market_sell(
            symbol=symbol, quantity=formatted_quantity
        )
        logger.info(
            f"LIVE SELL order successful for {symbol} for user {user_id}: {order}"
        )
        return order
    except BinanceAPIException as e:
        logger.error(
            f"LIVE SELL order failed for {symbol} for user {user_id}: {e.message}"
        )
        raise TradeError(f"Binance API Error on Sell: {e.message}")


async def quest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles the /quest command, providing price and RSI for a given symbol.
    Usage: /quest <SYMBOL> (e.g., /quest PEPEUSDT)
    """
    if not client:
        await update.message.reply_text(
            "The connection to the crypto realm (Binance) is not configured. Please check API keys."
        )
        return

    try:
        symbol = context.args[0].upper()
    except IndexError:
        await update.message.reply_text(
            "Please specify a trading pair. Usage: `/quest BTCUSDT`",
            parse_mode="Markdown",
        )
        return

    user_id = update.effective_user.id
    settings = db.get_user_effective_settings(user_id)

    # Check if a trade is already open or on the watchlist for this symbol
    if db.is_trade_open(user_id, symbol):
        await update.message.reply_text(
            f"You already have an open quest for {symbol}. Use /status to see it."
        )
        return

    if db.is_on_watchlist(user_id, symbol):
        await update.message.reply_text(
            f"You are already watching {symbol} for a dip. Use /status to check."
        )
        return

    await update.message.reply_text(
        f"LunessaSignals is gazing into the cosmic energies of {symbol}... 🔮"
    )
    price = get_current_price(symbol)
    rsi = get_rsi(symbol)

    if price is not None and rsi is not None:
        # initialize message accumulator
        message = ""
        message += "**\u2b50 Premium Signal!** The price has pierced the lower Bollinger Band while the RSI is low. A confluence of energies suggests a prime opportunity.\n\n"
        message += f"⚖️ **Hourly RSI(14):** `{rsi:.2f}`\n\n"

        # --- Premium Feature: Enhanced Buy Signal with Bollinger Bands ---
        is_bollinger_buy = False
        is_premium_user = settings.get("USE_BOLLINGER_BANDS")
        if settings.get("USE_BOLLINGER_BANDS"):
            _, _, lower_band, _ = get_bollinger_bands(
                symbol,
                period=settings.get("BOLL_PERIOD", 20),
                std_dev=settings.get("BOLL_STD_DEV", 2),
            )
            if lower_band:
                message += f"📊 **Lower Bollinger Band:** `${lower_band:,.8f}`\n\n"
                if price <= lower_band:
                    is_bollinger_buy = True

        # --- Determine Buy Condition based on user tier ---
        is_rsi_low = rsi < settings["RSI_BUY_THRESHOLD"]

        # For premium users, both conditions must be met. For free users, only RSI.
        should_add_to_watchlist = (
            is_premium_user and is_rsi_low and is_bollinger_buy
        ) or (not is_premium_user and is_rsi_low)

        if should_add_to_watchlist:
            db.add_to_watchlist(user_id=user_id, coin_symbol=symbol)

            if is_premium_user:  # This implies a strong, combined signal was found
                message += "**⭐ Premium Signal!** The price has pierced the lower Bollinger Band while the RSI is low. A confluence of energies suggests a prime opportunity.\n\n"

            message += (
                "The energies for {sym} are low. I will watch it for the perfect moment to strike (buy the dip) and notify you.\n\n".format(
                    sym=symbol
                )
                + "*I will automatically open a quest if the RSI shows signs of recovery.*"
            )
            if is_weekend():
                message += "\n\n*Note: Weekend trading can have lower volume and higher risk. Please trade with caution.*"
        elif rsi > settings["RSI_SELL_THRESHOLD"]:
            message += (
                "The energies are high. It may be a time to consider taking profits."
            )
        else:
            message += "The market is in balance. Patience is a virtue."

        message += "\n*New to trading?* Join Binance with my link!"

        await update.message.reply_text(message, parse_mode="Markdown")
    else:
        await update.message.reply_text(
            f"Could not retrieve data for {symbol}. Please ensure it's a valid symbol on Binance (e.g., BTCUSDT)."
        )


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /balance command."""
    user_id = update.effective_user.id
    mode, paper_balance = db.get_user_trading_mode_and_balance(user_id)

    if mode == "PAPER":
        await update.message.reply_text(
            f"You are in Paper Trading mode.\n💰 **Paper Balance:** ${paper_balance:,.2f} USDT",
            parse_mode="Markdown",
        )
        return

    # Live mode logic
    is_admin = user_id == config.ADMIN_USER_ID
    api_key, _ = db.get_user_api_keys(user_id)
    if not api_key and not is_admin:
        await update.message.reply_text(
            "Your Binance API keys are not set. Please use `/setapi <key> <secret>` in a private chat with me."
        )
        return

    await update.message.reply_text("Checking your treasure chest (Binance)...")
    try:
        balance = get_account_balance(user_id, asset="USDT")
        if balance is not None:
            await update.message.reply_text(
                f"You hold **{balance:.2f} USDT**.", parse_mode="Markdown"
            )
    except TradeError as e:
        # This will now catch the specific error message from the API
        await update.message.reply_text(
            f"Could not retrieve your balance.\n\n*Reason:* `{e}`\n\nPlease check your API key permissions and IP restrictions on Binance.",
            parse_mode="Markdown",
        )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /status command, showing open trades and wallet holdings."""
    user_id = update.effective_user.id
    mode, paper_balance = db.get_user_trading_mode_and_balance(user_id)

    message = f"✨ **Your Current Status ({mode} Mode)** ✨\n\n"

    # --- Display Open Trades ---
    open_trades = db.get_open_trades(user_id)
    if open_trades:
        message += "📊 **Open Quests:**\n"
        for trade_item in open_trades:
            symbol = trade_item["coin_symbol"]
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
        message += "📊 **Open Quests:** None\n\n"

    # --- Display Watchlist ---
    watchlist_items = db.get_all_watchlist_items_for_user(user_id)
    if watchlist_items:
        message += "👀 **Watching for Dips:**\n"
        for item in watchlist_items:
            message += f"- **{item['coin_symbol']}** (Added: {item['add_timestamp']})\n"
        message += "\n"
    else:
        message += "👀 **Watching for Dips:** None\n\n"

    # --- Display Wallet Holdings (Live Mode Only) ---
    if mode == "LIVE":
        message += "💰 **Wallet Holdings:**\n"
        try:
            wallet_balances = get_all_spot_balances(user_id)
            if wallet_balances:
                # Get symbols from open trades for differentiation
                open_trade_symbols = {
                    trade_item["coin_symbol"].replace("USDT", "")
                    for trade_item in open_trades
                }

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
            message += f"  *Could not retrieve wallet balances: {e.message}*\n"
        except Exception as e:
            logger.error(f"Unexpected error fetching wallet balances for status: {e}")
            message += (
                "  *An unexpected error occurred while fetching wallet balances.*\n"
            )
    elif mode == "PAPER":
        message += f"💰 **Paper Balance:** ${paper_balance:,.2f} USDT\n"

    # --- Autotrade skipped events summary (if Redis available) ---
    try:
        rc = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
        skipped_count = rc.hget("autotrade:stats", "skipped_events") or rc.hget(
            "autotrade:stats", "skipped_cycles"
        )
        if skipped_count:
            message += f"\n⚠️ Autotrade skipped events: {skipped_count}\n"
            # show a short sample of recent skipped events
            try:
                raw = rc.lrange("autotrade:skipped_events", 0, 4)
                if raw:
                    message += "Recent skipped events:\n"
                    for it in raw:
                        try:
                            ev = json.loads(it)
                            ts = ev.get("ts")
                            sym = ev.get("symbol") or ev.get("reason")
                            message += f" - {sym} at {ts}\n"
                        except Exception:
                            message += f" - {it}\n"
            except Exception:
                pass
    except Exception:
        # Ignore if Redis not configured
        pass

    # --- Cached Gemini suggestions (if gemini_cache available) ---
    try:
        try:
            from gemini_cache import get_suggestions_for

            cached = get_suggestions_for(config.AI_MONITOR_COINS[:5])
            if cached:
                message += "\n🤖 Cached AI suggestions:\n"
                for s, d in (cached or {}).items():
                    message += f" - {s}: {d}\n"
        except Exception:
            pass
    except Exception:
        pass

    await update.message.reply_text(message, parse_mode="Markdown")


async def import_last_trade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /import command to manually add a trade or import from Binance."""
    user_id = update.effective_user.id
    mode, _ = db.get_user_trading_mode_and_balance(user_id)

    # Runtime guard: block import if Binance client unavailable
    try:
        if not BINANCE_AVAILABLE:
            await update.message.reply_text(
                "Live trading/import is currently disabled because the Binance client is unavailable. Please check /binance_status."
            )
            return
    except Exception:
        await update.message.reply_text(
            "Live trading/import is currently unavailable. Please try again later."
        )
        return

    if mode == "PAPER":
        await update.message.reply_text(
            "Trade import is only available in LIVE trading mode."
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Please specify a symbol, price, and quantity.\n"
            "Usage: `/import <SYMBOL> <PRICE> <QUANTITY>`\n"
            "Example: `/import BTCUSDT 30000 0.01`\n"
            "You can also use `/import <SYMBOL>` to auto-import your last Binance trade for that symbol.",
            parse_mode="Markdown",
        )
        return

    symbol = context.args[0].upper()
    buy_price = None
    quantity = None

    # Robust validation for symbol format and existence on Binance
    if not re.fullmatch(r"[A-Z0-9]+(USDT|BTC)", symbol):
        await update.message.reply_text(
            f"Invalid symbol format: `{symbol}`. Please use a valid Binance trading pair like `BTCUSDT` or `ETHBTC`.",
            parse_mode="Markdown",
        )
        return

    # Check if symbol exists on Binance
    symbol_info = get_symbol_info(symbol)
    if not symbol_info:
        await update.message.reply_text(
            f"Symbol `{symbol}` does not exist on Binance or is not available for trading. Please check the symbol and try again.",
            parse_mode="Markdown",
        )
        return

    try:
        if len(context.args) > 1:
            # Manual import with price and quantity (optional)
            buy_price = float(context.args[1])
            if len(context.args) > 2:
                quantity = float(context.args[2])
            else:
                # If price is given but quantity is not, try to get it from Binance
                last_trade = get_last_trade_from_binance(user_id, symbol)
                if last_trade and float(last_trade["price"]) == buy_price:
                    quantity = float(last_trade["qty"])
                else:
                    await update.message.reply_text(
                        "For manual import, if quantity is not provided, the given price must match your last Binance trade for that symbol."
                    )
                    return
        else:
            # Auto-import from Binance
            await update.message.reply_text(
                f"Attempting to import your last trade for {symbol} from Binance..."
            )
            last_trade = get_last_trade_from_binance(user_id, symbol)

            if not last_trade:
                await update.message.reply_text(
                    f"Could not find a recent trade for {symbol} on Binance. Please specify the buy price and quantity manually: `/import {symbol} <PRICE> <QUANTITY>`.",
                    parse_mode="Markdown",
                )
                return

            buy_price = float(last_trade["price"])
            quantity = float(last_trade["qty"])
            logger.info(
                f"Imported trade for {symbol}: price={buy_price}, quantity={quantity}"
            )

        if buy_price and quantity:
            # Calculate stop loss and take profit based on current settings
            settings = db.get_user_effective_settings(user_id)
            stop_loss_price = buy_price * (1 - settings["STOP_LOSS_PERCENTAGE"] / 100)
            take_profit_price = buy_price * (
                1 + settings["PROFIT_TARGET_PERCENTAGE"] / 100
            )

            trade_id = db.log_trade(
                user_id=user_id,
                coin_symbol=symbol,
                buy_price=buy_price,
                stop_loss=stop_loss_price,
                take_profit=take_profit_price,
                mode="LIVE",
                quantity=quantity,
                rsi_at_buy=None,
            )  # RSI at buy is not available for imported trades

            await update.message.reply_text(
                f"✅ **Trade Imported!**\n\n"
                f"   - **{symbol}** (ID: {trade_id})\n"
                f"   - Bought at: `${buy_price:,.8f}`\n"
                f"   - Quantity: `{quantity:.4f}`\n"
                f"   - ✅ Take Profit: `${take_profit_price:,.8f}`\n"
                f"   - 🛡️ Stop Loss: `${stop_loss_price:,.8f}`\n\n"
                f"This trade will now be monitored. Use /status to see your open quests.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "Could not determine trade details for import."
            )

    except ValueError:
        await update.message.reply_text(
            "Invalid price or quantity. Please ensure they are numbers."
        )
    except TradeError as e:
        await update.message.reply_text(f"Error importing trade: {e}")
    except Exception as e:
        logger.error(
            f"An unexpected error occurred during trade import: {e}", exc_info=True
        )
        await update.message.reply_text(
            "An unexpected error occurred while importing the trade."
        )


async def close_trade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually closes an open trade by its ID."""
    user_id = update.effective_user.id

    # Runtime guard: block close if Binance client unavailable for LIVE trades
    try:
        if not BINANCE_AVAILABLE:
            await update.message.reply_text(
                "Live trading/close is currently disabled because the Binance client is unavailable. Please check /binance_status."
            )
            return
    except Exception:
        await update.message.reply_text(
            "Live trading is currently unavailable. Please try again later."
        )
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Please provide the ID of the trade you want to close. Usage: `/close <TRADE_ID>`",
            parse_mode="Markdown",
        )
        return

    trade_id = int(context.args[0])
    trade_to_close = db.get_trade_by_id(trade_id)

    if (
        not trade_to_close
        or trade_to_close["user_id"] != user_id
        or trade_to_close.get("close_timestamp")
    ):
        await update.message.reply_text(
            f"Trade with ID `{trade_id}` not found or already closed.",
            parse_mode="Markdown",
        )
        return

    symbol = trade_to_close["coin_symbol"]
    buy_price = trade_to_close["buy_price"]
    quantity = trade_to_close["quantity"]
    mode = trade_to_close["mode"]

    current_price = get_current_price(symbol)
    if not current_price:
        await update.message.reply_text(
            f"Could not get current price for {symbol}. Please try again."
        )
        return

    pnl_percent = ((current_price - buy_price) / buy_price) * 100
    profit_usdt = (current_price - buy_price) * quantity if quantity else 0.0

    close_reason = "Manual Close"
    win_loss = (
        "win" if pnl_percent > 0 else ("loss" if pnl_percent < 0 else "break_even")
    )

    if mode == "LIVE":
        try:
            # Attempt to sell on Binance
            if quantity and quantity > 0:
                await update.message.reply_text(
                    f"Attempting to sell {quantity:.4f} of {symbol} on Binance..."
                )
                place_sell_order(user_id, symbol, quantity)
                db.close_trade(
                    trade_id=trade_id,
                    user_id=user_id,
                    sell_price=current_price,
                    close_reason=close_reason,
                    win_loss=win_loss,
                    pnl_percentage=pnl_percent,
                )
                log_trade_outcome(symbol, pnl_percent)
                update_daily_pl(profit_usdt, db)
                await update.message.reply_text(
                    f"✅ **Trade Closed!**\n\n"
                    f"Your **{symbol}** quest (ID: {trade_id}) was manually closed at `${current_price:,.8f}`.\n\n"
                    f"   - **P/L:** `{pnl_percent:+.2f}%` (`${profit_usdt:,.2f}` USDT)\n\n"
                    f"Your position on Binance has been sold.",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(
                    f"Cannot close trade {trade_id} on Binance: quantity is zero or not recorded."
                )
                db.close_trade(
                    trade_id=trade_id,
                    user_id=user_id,
                    sell_price=current_price,
                    close_reason=close_reason,
                    win_loss=win_loss,
                    pnl_percentage=pnl_percent,
                )
                log_trade_outcome(symbol, pnl_percent)
                update_daily_pl(profit_usdt, db)
                await update.message.reply_text(
                    f"✅ **Trade Closed (Database Only)!**\n\n"
                    f"Your **{symbol}** quest (ID: {trade_id}) was manually closed at `${current_price:,.8f}`.\n\n"
                    f"   - **P/L:** `{pnl_percent:+.2f}%` (`${profit_usdt:,.2f}` USDT)\n\n"
                    f"*Note: No Binance sale was executed as quantity was not found or zero.*",
                    parse_mode="Markdown",
                )

        except TradeError as e:
            await update.message.reply_text(
                f"⚠️ **Failed to close trade {trade_id} on Binance.**\n\n*Reason:* `{e}`\n\nThe trade remains open in the bot's records. Please try again or close manually on Binance.",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(
                f"An unexpected error occurred during manual trade close for {trade_id}: {e}",
                exc_info=True,
            )
            await update.message.reply_text(
                "An unexpected error occurred while trying to close the trade."
            )

    elif mode == "PAPER":
        success = db.close_trade(
            trade_id=trade_id,
            user_id=user_id,
            sell_price=current_price,
            close_reason=close_reason,
            win_loss=win_loss,
            pnl_percentage=pnl_percent,
        )
        if success:
            log_trade_outcome(symbol, pnl_percent)
            db.update_paper_balance(
                user_id, profit_usdt
            )  # Update paper balance with profit/loss
            await update.message.reply_text(
                f"✅ **Paper Trade Closed!**\n\n"
                f"Your **{symbol}** paper quest (ID: {trade_id}) was manually closed at `${current_price:,.8f}`.\n\n"
                f"   - **P/L:** `{pnl_percent:+.2f}%` (`${profit_usdt:,.2f}` USDT)\n\n"
                f"Your paper balance has been updated.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(f"Failed to close paper trade {trade_id}.")


async def check_btc_volatility_and_alert(context: ContextTypes.DEFAULT_TYPE):
    """
    Checks BTC's recent price movement and sends an alert if it's significant.
    This helps users understand the overall market pressure.
    """
    try:
        # Fetch the last 2 closed hourly candles for BTC
        klines = client.get_historical_klines(
            "BTCUSDT", Client.KLINE_INTERVAL_1HOUR, "2 hours ago UTC"
        )
        if len(klines) < 2:
            logger.warning("Not enough BTC kline data to check for volatility.")
            return

        # klines[0] is the older candle, klines[1] is the most recent closed candle
        old_price = float(klines[0][4])  # Close price of the n-2 candle
        new_price = float(klines[1][4])  # Close price of the n-1 candle

        percent_change = ((new_price - old_price) / old_price) * 100

        # Initialize bot_data for state management if it doesn't exist
        if "market_state" not in context.bot_data:
            context.bot_data["market_state"] = "CALM"

        last_state = context.bot_data.get("market_state", "CALM")
        current_state = "CALM"
        alert_message = None

        if percent_change > config.BTC_ALERT_THRESHOLD_PERCENT:
            current_state = "BTC_PUMP"
            if last_state != "BTC_PUMP":
                alert_message = (
                    "🚨 **Market Alert: BTC Pumping** 🚨\n\n"
                    "Bitcoin has increased by **{pct:.2f}%** in the last hour.\n\n"
                    "This could lead to volatility in altcoins. Please review your open positions carefully.".format(
                        pct=percent_change
                    )
                )
        elif percent_change < -config.BTC_ALERT_THRESHOLD_PERCENT:
            current_state = "BTC_DUMP"
            if last_state != "BTC_DUMP":
                alert_message = (
                    "🚨 **Market Alert: BTC Dumping** 🚨\n\n"
                    "Bitcoin has decreased by **{pct:.2f}%** in the last hour.\n\n"
                    "This could lead to significant drops in altcoins. Please review your open positions carefully.".format(
                        pct=percent_change
                    )
                )

        # If the market has calmed down after being volatile
        if current_state == "CALM" and last_state != "CALM":
            alert_message = (
                "✅ **Market Update: BTC Stabilizing** ✅\n\n"
                "Bitcoin's movement has stabilized. The previous period of high volatility appears to be over."
            )

        if alert_message and config.CHAT_ID:
            try:
                await context.bot.send_message(
                    chat_id=config.CHAT_ID, text=alert_message, parse_mode="Markdown"
                )
                logger.info(
                    "Sent market alert to CHAT_ID %s. New state: %s",
                    config.CHAT_ID,
                    current_state,
                )
                context.bot_data["market_state"] = current_state
            except Exception as e:
                logger.error(
                    f"Failed to send market alert to CHAT_ID {config.CHAT_ID}: {e}"
                )
        elif current_state != last_state:
            context.bot_data["market_state"] = current_state

    except BinanceAPIException as e:
        logger.error(f"Binance API error during market volatility check: {e}")
    except Exception as e:
        logger.error(
            f"An unexpected error occurred during market volatility check: {e}"
        )


async def check_watchlist_for_buys(
    context: ContextTypes.DEFAULT_TYPE, prices: dict, indicator_cache: dict
):
    """Monitors coins on the watchlist to find a dip-buy opportunity."""
    watchlist_items = db.get_all_watchlist_items()
    if not watchlist_items:
        return

    logger.info(
        f"Checking {len(watchlist_items)} item(s) on the watchlist for dip-buy opportunities..."
    )

    now = datetime.now(timezone.utc)

    for item in watchlist_items:
        symbol = item["coin_symbol"]
        item_id = item["id"]
        user_id = item["user_id"]
        settings = db.get_user_effective_settings(user_id)

        # Check for timeout
        add_time = datetime.strptime(
            item["add_timestamp"], "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)
        hours_passed = (now - add_time).total_seconds() / 3600
        if hours_passed > config.WATCHLIST_TIMEOUT_HOURS:
            db.remove_from_watchlist(item_id)
            logger.info(
                f"Removed {symbol} from watchlist for user {user_id} due to timeout."
            )
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"⏳ Your watch on **{symbol}** has expired without a buy signal. The opportunity has passed for now.",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error(
                    f"Failed to send watchlist timeout notification to user {user_id}: {e}"
                )
            continue

        # Check for buy signal (RSI recovery)
        if symbol not in indicator_cache:
            try:
                indicator_cache[symbol] = {"rsi": get_rsi(symbol)}
                time.sleep(0.1)  # Stagger API calls to be safe
            except BinanceAPIException as e:
                logger.warning(
                    f"API error getting RSI for watchlist item {symbol}: {e}"
                )
                continue

        cached_data = indicator_cache.get(symbol, {})
        current_rsi = cached_data.get("rsi")

        if current_rsi and current_rsi > settings.get(
            "RSI_BUY_RECOVERY_THRESHOLD", config.RSI_BUY_RECOVERY_THRESHOLD
        ):
            # We have a buy signal!
            buy_price = prices.get(symbol)
            if not buy_price:
                logger.warning(
                    f"Could not get price for {symbol} to execute watchlist buy. Will retry."
                )
                continue

            mode, paper_balance = db.get_user_trading_mode_and_balance(user_id)

            # --- Risk Management: Pause trading if daily drawdown exceeded ---
            account_balance = get_account_balance(user_id, "USDT")
            if should_pause_trading(
                db, account_balance, getattr(config, "MAX_DAILY_DRAWDOWN_PERCENT", 0.10)
            ):
                logger.info(
                    f"Trading paused for user {user_id} due to daily drawdown limit."
                )
                continue

            if mode == "LIVE":
                usdt_balance = account_balance
                trade_size_usdt = float(settings.get("TRADE_SIZE_USDT", 5.0))
                if usdt_balance is None or usdt_balance < trade_size_usdt:
                    logger.info(
                        f"User {user_id} has insufficient LIVE USDT balance ({usdt_balance}) to open trade for {symbol} with trade size {trade_size_usdt}."
                    )
                    continue

                # --- ATR-based stop-loss ---
                klines = client.get_historical_klines(
                    symbol, Client.KLINE_INTERVAL_1HOUR, "30 hours ago UTC"
                )
                atr = calc_atr(klines, period=14) if klines else None

                try:
                    order, entry_price, quantity = place_buy_order(
                        user_id, symbol, trade_size_usdt
                    )
                    stop_loss_price = (
                        get_atr_stop(
                            entry_price,
                            atr,
                            getattr(config, "ATR_STOP_MULTIPLIER", 1.5),
                        )
                        if atr
                        else entry_price * (1 - settings["STOP_LOSS_PERCENTAGE"] / 100)
                    )
                    take_profit_price = entry_price * (
                        1 + settings["PROFIT_TARGET_PERCENTAGE"] / 100
                    )
                    db.log_trade(
                        user_id=user_id,
                        coin_symbol=symbol,
                        buy_price=entry_price,
                        stop_loss=stop_loss_price,
                        take_profit=take_profit_price,
                        mode="LIVE",
                        quantity=quantity,
                        rsi_at_buy=current_rsi,
                    )
                    db.remove_from_watchlist(item_id)
                    logger.info(
                        f"Executed LIVE dip-buy for {symbol} for user {user_id} at price {entry_price}"
                    )

                    message = (
                        f"🎯 **Live Quest Started!** 🎯\n\n"
                        f"LunessaSignals has executed a **LIVE** buy for **{quantity:.4f} {symbol}** after spotting a recovery!\n\n"
                        f"   - Bought at: `${entry_price:,.8f}`\n"
                        f"   - ✅ Take Profit: `${take_profit_price:,.8f}`\n"
                        f"   - 🛡️ Stop Loss: `${stop_loss_price:,.8f}`\n\n"
                        f"Use /status to see your open quests."
                    )
                    await context.bot.send_message(
                        chat_id=user_id, text=message, parse_mode="Markdown"
                    )
                except TradeError as e:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"⚠️ **Live Buy FAILED** for {symbol}.\n\n*Reason:* `{e}`\n\nPlease check your account balance and API key permissions.",
                        parse_mode="Markdown",
                    )

            elif mode == "PAPER":
                trade_size_usdt = config.PAPER_TRADE_SIZE_USDT
                if paper_balance < trade_size_usdt:
                    logger.info(
                        f"User {user_id} has insufficient paper balance to open trade for {symbol}."
                    )
                    continue

                db.update_paper_balance(user_id, -trade_size_usdt)

                entry_price = buy_price
                stop_loss_price = entry_price * (
                    1 - settings["STOP_LOSS_PERCENTAGE"] / 100
                )
                take_profit_price = entry_price * (
                    1 + settings["PROFIT_TARGET_PERCENTAGE"] / 100
                )
                db.log_trade(
                    user_id=user_id,
                    coin_symbol=symbol,
                    buy_price=entry_price,
                    stop_loss=stop_loss_price,
                    take_profit=take_profit_price,
                    mode="PAPER",
                    trade_size_usdt=trade_size_usdt,
                )
                db.remove_from_watchlist(item_id)
                logger.info(
                    f"Executed PAPER dip-buy for {symbol} for user {user_id} at price {entry_price}"
                )

                message = (
                    f"🎯 **Paper Quest Started!** 🎯\n\n"
                    f"LunessaSignals has opened a new **PAPER** quest for **{symbol}** after spotting a recovery!\n\n"
                    f"   - Bought at: `${entry_price:,.8f}`\n"
                    f"   - ✅ Take Profit: `${take_profit_price:,.8f}`\n"
                    f"   - 🛡️ Stop Loss: `${stop_loss_price:,.8f}`\n\n"
                    f"Use /status to see your open quests."
                )
                await context.bot.send_message(
                    chat_id=user_id, text=message, parse_mode="Markdown"
                )


async def ai_trade_monitor(
    context: ContextTypes.DEFAULT_TYPE, symbol: str, user_id: int
):
    """The core AI logic to automatically open trades based on market signals for a single symbol."""
    try:
        settings = db.get_user_effective_settings(user_id)

        # --- Gemini AI Signal ---
        gemini_info = await gemini_cacher.ask_gemini_for_symbol(symbol)
        gemini_signal = gemini_info.get("signal", "neutral").lower()

        # --- Indicator Signals ---
        klines = client.get_historical_klines(
            symbol, Client.KLINE_INTERVAL_1HOUR, "100 hours ago UTC"
        )
        if not klines or len(klines) < 20:
            logger.info(f"Not enough kline data for {symbol}")
            return

        df = pd.DataFrame(
            klines,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_asset_volume",
                "number_of_trades",
                "taker_buy_base_asset_volume",
                "taker_buy_quote_asset_volume",
                "ignore",
            ],
        )
        for col in ["high", "low", "close"]:
            df[col] = pd.to_numeric(df[col])

        rsi = calculate_rsi(df["close"], period=settings.get("RSI_PERIOD", 14)).iloc[-1]

        # --- Buy Logic ---
        should_buy = False
        if rsi < settings.get("RSI_BUY_THRESHOLD", 30):
            should_buy = True
        elif gemini_signal == "buy":
            should_buy = True

        if not should_buy:
            return

        # --- Execute Buy ---
        settings = db.get_user_effective_settings(user_id)
        trade_size_usdt = float(settings.get("TRADE_SIZE_USDT", 5.0))
        if trade_size_usdt < 5.0:
            trade_size_usdt = 5.0

        usdt_balance = get_account_balance(user_id, "USDT")
        if usdt_balance is None or usdt_balance < trade_size_usdt:
            logger.info(
                f"Skipping {symbol}: USDT balance {usdt_balance} too low for trade size {trade_size_usdt}."
            )
            return

        order, entry_price, quantity = place_buy_order(user_id, symbol, trade_size_usdt)
        stop_loss_price = entry_price * (1 - settings["STOP_LOSS_PERCENTAGE"] / 100)
        take_profit_price = entry_price * (
            1 + settings["PROFIT_TARGET_PERCENTAGE"] / 100
        )
        db.log_trade(
            user_id=user_id,
            coin_symbol=symbol,
            buy_price=entry_price,
            stop_loss=stop_loss_price,
            take_profit=take_profit_price,
            mode="LIVE",
            quantity=quantity,
            rsi_at_buy=rsi,
            peak_price=entry_price,
        )

        message = (
            f"🤖 **AI Autotrade Initiated!** 🤖\n\n"
            f"Detected a high-confidence buy signal for **{symbol}**.\n\n"
            f"   - Bought: **{quantity:.4f} {symbol}** at `${entry_price:,.8f}`\n"
            f"   - Value: `${trade_size_usdt:,.2f}` USDT\n"
            f"   - Strategy: RSI ({rsi:.2f}) & Gemini ({gemini_signal})\n\n"
            f"Use /status to monitor this new quest."
        )
        await context.bot.send_message(
            chat_id=user_id, text=message, parse_mode="Markdown"
        )

    except TradeError as e:
        logger.error(f"AI failed to execute buy for {symbol}: {e}")
        await context.bot.send_message(
            chat_id=user_id,
            text=f"⚠️ **AI Autotrade FAILED** for {symbol}.\n*Reason:* `{e}`",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(
            f"An unexpected error occurred in AI trade monitor for {symbol}: {e}",
            exc_info=True,
        )


async def run_monitoring_cycle(
    context: ContextTypes.DEFAULT_TYPE, open_trades, prices, indicator_cache
):
    """
    The intelligent core of the bot. Called by the JobQueue to:
    1. Monitor overall market conditions based on BTC movement.
    2. Check all open trades against their Stop-Loss and Take-Profit levels.
    """
    if not client:
        logger.warning("Market monitor skipped: Binance client not configured.")
        return

    logger.info(f"Monitoring {len(open_trades)} open trade(s)...")
    datetime.now(timezone.utc)
    for trade in open_trades:
        # Use dict-style access for sqlite3.Row
        trade["mode"] if "mode" in trade.keys() else None
        user_id = trade["user_id"] if "user_id" in trade.keys() else None
        symbol = trade["coin_symbol"] if "coin_symbol" in trade.keys() else None
        # Normalize symbol casing coming from DB; many imports mix case (bnbusdt vs BNBUSDT)
        if symbol:
            try:
                symbol_upper = symbol.upper()
            except Exception:
                symbol_upper = symbol
        else:
            symbol_upper = symbol
        symbol = symbol_upper
        if not symbol or symbol not in prices:
            continue

        current_price = prices[symbol]
        pnl_percent = ((current_price - trade["buy_price"]) / trade["buy_price"]) * 100

        try:
            settings = db.get_user_effective_settings(user_id)
        except IndexError:
            logger.error(
                f"No settings found for user_id {user_id}, using default settings."
            )
            settings = db.get_user_effective_settings(None)

        # Validate trade object for required keys
        # Treat missing or non-positive quantities as invalid and record them for diagnostics
        bad_quantity = False
        try:
            q = trade.get("quantity") if hasattr(trade, "get") else trade["quantity"]
        except Exception:
            q = None

        if q is None or (isinstance(q, (int, float)) and q <= 0):
            bad_quantity = True

        if bad_quantity:
            try:
                trade_id = trade["id"]
            except Exception:
                trade_id = "unknown"
            # Build a slim, JSON-serializable dict for logging
            try:
                trade_repr = dict(trade)
            except Exception:
                # sqlite3.Row sometimes doesn't convert directly
                trade_repr = (
                    {k: trade[k] for k in trade.keys()}
                    if hasattr(trade, "keys")
                    else str(trade)
                )

            logger.error(
                f"[user_id={user_id}][trade_id={trade_id}][symbol={symbol}] Missing/invalid quantity in trade: {trade_repr}"
            )

            # Push diagnostic entry to Redis (non-fatal)
            try:
                if getattr(_config, "REDIS_URL", None):
                    rc = redis.from_url(_config.REDIS_URL, decode_responses=True)
                    rc.lpush(
                        "trade_issues",
                        json.dumps(
                            {
                                "trade_id": trade_id,
                                "user_id": user_id,
                                "symbol": symbol,
                                "quantity": q,
                                "row": trade_repr,
                                "ts": int(time.time()),
                            }
                        ),
                    )
                    # Keep list reasonably sized
                    rc.ltrim("trade_issues", 0, 999)
            except Exception as e:
                logger.debug(f"Failed to record trade issue to Redis: {e}")

            # Optionally notify admin (opt-in via config flag)
            try:
                if getattr(_config, "NOTIFY_ADMIN_ON_TRADE_ISSUE", False) and getattr(
                    _config, "ADMIN_USER_ID", None
                ):
                    admin_id = _config.ADMIN_USER_ID
                    # context is available in this function; send a short alert
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=f"Detected invalid trade qty for user {user_id}, trade {trade_id}, symbol {symbol}. See trade_issues list.",
                    )
            except Exception:
                # Never escalate on notification failure
                pass

            # Skip this trade during monitoring to avoid runtime errors
            continue

        # Notional guard before any sell/close logic
        trade["quantity"] * current_price
        if not TradeValidator.is_trade_valid(
            symbol,
            trade["quantity"],
            current_price,
            user_id=user_id,
            slip_id=trade.get("id"),
        ):
            continue

        # --- DSLA Logic ---
        # ... (existing DSLA logic is fine)

        # --- RSI Exit Logic ---
        if pnl_percent > 0:  # Only consider exiting profitable trades with RSI
            if symbol not in indicator_cache:
                indicator_cache[symbol] = {"rsi": get_rsi(symbol)}

            current_rsi = (
                indicator_cache[symbol]["rsi"]
                if "rsi" in indicator_cache[symbol]
                else None
            )
            rsi_sell_threshold = settings.get("RSI_BEARISH_EXIT_THRESHOLD", 65.0)
            rsi_overbought = settings.get("RSI_OVERBOUGHT_ALERT_THRESHOLD", 80.0)

            # We need to know if RSI was recently overbought
            # For simplicity, we check if the rsi_at_buy was high, or we can query history
            rsi_at_buy = (
                trade["rsi_at_buy"] if "rsi_at_buy" in trade.keys() else rsi_overbought
            )

            if (
                rsi_at_buy >= rsi_overbought
                and current_rsi is not None
                and current_rsi < rsi_sell_threshold
            ):
                if "buy_price" in trade and "quantity" in trade:
                    profit_usdt = (current_price - trade["buy_price"]) * trade[
                        "quantity"
                    ]
                    notification = (
                        f"📉 **RSI Exit Triggered!** Your {symbol} quest (ID: {trade['id']}) was closed at `${current_price:,.8f}`.\n\n"
                        f"   - **P/L:** `{pnl_percent:.2f}%` (`${profit_usdt:,.2f}` USDT)\n"
                        f"   - Current RSI: `{current_rsi:.2f}`"
                    )
                    db.close_trade(
                        trade_id=trade["id"],
                        user_id=user_id,
                        sell_price=current_price,
                        close_reason="RSI Exit",
                        win_loss="win",
                        pnl_percentage=pnl_percent,
                    )
                    log_trade_outcome(symbol, pnl_percent)
                    update_daily_pl(profit_usdt, db)
                    await context.bot.send_message(
                        chat_id=user_id, text=notification, parse_mode="Markdown"
                    )
                else:
                    # Only skip silently, no warning or notification
                    continue  # Move to next trade

        # --- Stop-Loss and Take-Profit checks ---
        if current_price <= trade["stop_loss_price"]:
            profit_usdt = (current_price - trade["buy_price"]) * trade["quantity"]
            db.close_trade(
                trade_id=trade["id"],
                user_id=user_id,
                sell_price=current_price,
                close_reason="Stop-Loss",
                win_loss="loss",
                pnl_percentage=pnl_percent,
            )
            log_trade_outcome(symbol, pnl_percent)
            update_daily_pl(profit_usdt, db)
            notification = f"🛡️ **Stop-Loss Triggered!** Your {symbol} quest (ID: {trade['id']}) was closed at `${current_price:,.8f}` (P/L: {pnl_percent:.2f}%)."
            await context.bot.send_message(
                chat_id=user_id, text=notification, parse_mode="Markdown"
            )
            continue

        if current_price >= trade["take_profit_price"]:
            profit_usdt = (current_price - trade["buy_price"]) * trade["quantity"]
            db.close_trade(
                trade_id=trade["id"],
                user_id=user_id,
                sell_price=current_price,
                close_reason="Take-Profit",
                win_loss="win",
                pnl_percentage=pnl_percent,
            )
            log_trade_outcome(symbol, pnl_percent)
            update_daily_pl(profit_usdt, db)
            notification = f"🏆 **Take-Profit Hit!** Your {symbol} quest (ID: {trade['id']}) was closed at `${current_price:,.8f}` (P/L: {pnl_percent:.2f}%)."
            await context.bot.send_message(
                chat_id=user_id, text=notification, parse_mode="Markdown"
            )
            continue


async def prefetch_prices(open_trades: list) -> dict:
    """Fetches current prices for all symbols in open trades."""
    prices = {}
    symbols_to_fetch = {trade["coin_symbol"] for trade in open_trades}
    for symbol in symbols_to_fetch:
        price = get_current_price(symbol)
        if price:
            prices[symbol] = price
        await asyncio.sleep(0.05)  # Small delay to avoid hitting rate limits
    return prices


async def prefetch_indicators(open_trades: list) -> dict:
    """Fetches indicators for all symbols in open trades."""
    indicator_cache = {}
    symbols_to_fetch = {trade["coin_symbol"] for trade in open_trades}
    for symbol in symbols_to_fetch:
        # Only fetch RSI for now, as it's used in the exit logic
        rsi = get_rsi(symbol)
        if rsi:
            indicator_cache[symbol] = {"rsi": rsi}
        await asyncio.sleep(0.05)  # Small delay to avoid hitting rate limits
    return indicator_cache


async def scheduled_monitoring_job(context: ContextTypes.DEFAULT_TYPE):
    """
    This is the wrapper function called by APScheduler.
    It gathers the latest data and then calls the main monitoring logic.
    """
    logger.info("Running scheduled_monitoring_job...")
    user_id = config.ADMIN_USER_ID  # Assuming monitoring is for the admin user
    logger.info(f"Admin user ID from config: {user_id}")
    if user_id:
        autotrade_status = db.get_autotrade_status(user_id)
        logger.info(f"Autotrade status for admin user: {autotrade_status}")
        if not autotrade_status:
            logger.info(
                "Scheduled monitoring skipped: Autotrade disabled for admin user."
            )
            return
    else:
        logger.info("Scheduled monitoring skipped: Admin user not set.")
        return

    try:
        # 1. Gather all the data needed for open trades
        open_trades = db.get_open_trades(user_id)
        prices = await prefetch_prices(open_trades)
        indicator_cache = await prefetch_indicators(open_trades)

        # 2. Monitor open trades
        if open_trades:
            await run_monitoring_cycle(context, open_trades, prices, indicator_cache)

        # 3. Scan for new trades
        monitored_coins = get_monitored_coins()
        for symbol in monitored_coins:
            if not db.is_trade_open(user_id, symbol):
                await ai_trade_monitor(context, symbol, user_id)
                await asyncio.sleep(1)  # Stagger API calls

    except Exception as e:
        logger.error(f"Error in scheduled_monitoring_job: {e}", exc_info=True)


async def adaptive_strategy_job():
    """Periodically analyze trade history and adapt strategy parameters."""
    logger.info("Running adaptive strategy job...")
    conn = db.get_db_connection()
    cursor = conn.execute(
        "SELECT rsi_at_buy, pnl_percentage, coin_symbol FROM trades WHERE status = 'closed' AND rsi_at_buy IS NOT NULL AND pnl_percentage IS NOT NULL"
    )
    rows = cursor.fetchall()
    if not rows:
        logger.info("No closed trades with RSI and PnL data for learning.")
        return
    # Analyze RSI thresholds
    profitable_rsi = [row["rsi_at_buy"] for row in rows if row["pnl_percentage"] > 0]
    if profitable_rsi:
        new_rsi_threshold = int(statistics.median(profitable_rsi))
        config.LAST_LEARNED_RSI_THRESHOLD = new_rsi_threshold
        logger.info(
            f"Adaptive strategy: Updated RSI buy threshold to {new_rsi_threshold}"
        )
    # Analyze best coins
    coin_pnl = {}
    for row in rows:
        coin = row["coin_symbol"]
        coin_pnl.setdefault(coin, []).append(row["pnl_percentage"])
    avg_coin_pnl = {c: statistics.mean(pnls) for c, pnls in coin_pnl.items() if pnls}
    best_coins = sorted(avg_coin_pnl, key=avg_coin_pnl.get, reverse=True)[:5]
    config.ADAPTIVE_TOP_COINS = best_coins
    logger.info(f"Adaptive strategy: Top performing coins: {best_coins}")
    # Optionally, adjust allocation or other parameters here
    # ...existing code...


def get_micro_vwap(symbol, interval=Client.KLINE_INTERVAL_1MINUTE, window=20):
    """Calculates Micro-VWAP (short-term VWAP) for a given symbol."""
    try:
        klines = client.get_historical_klines(
            symbol, interval, f"{window} minutes ago UTC"
        )
        if len(klines) < window:
            return None
        prices = np.array([float(k[4]) for k in klines])
        volumes = np.array([float(k[5]) for k in klines])
        vwap = np.sum(prices * volumes) / np.sum(volumes)
        return vwap
    except Exception as e:
        logger.error(f"Error calculating Micro-VWAP for {symbol}: {e}")
        return None


def get_bid_ask_volume_ratio(symbol, interval=Client.KLINE_INTERVAL_1MINUTE, window=20):
    """Estimates bid/ask volume ratio using kline buy/sell volume approximation."""
    try:
        klines = client.get_historical_klines(
            symbol, interval, f"{window} minutes ago UTC"
        )
        if len(klines) < window:
            return None
        buy_volumes = np.array([float(k[9]) for k in klines])  # taker buy volume
        total_volumes = np.array([float(k[5]) for k in klines])
        sell_volumes = total_volumes - buy_volumes
        ratio = np.sum(buy_volumes) / (np.sum(sell_volumes) + 1e-8)
        return ratio
    except Exception as e:
        logger.error("Error calculating bid/ask volume ratio for %s: %s", symbol, e)
        return None


def get_mad(symbol, interval=Client.KLINE_INTERVAL_1HOUR, window=20):
    """Calculates Mean Absolute Deviation (MAD) for a given symbol."""
    try:
        klines = client.get_historical_klines(
            symbol, interval, f"{window} hours ago UTC"
        )
        if len(klines) < window:
            return None
        closes = np.array([float(k[4]) for k in klines])
        mean = np.mean(closes)
        mad = np.mean(np.abs(closes - mean))
        return mad
    except Exception as e:
        logger.error("Error calculating MAD for %s: %s", symbol, e)
        return None


# Scheduler setup
# Schedule the adaptive strategy job (example: every 6 hours)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()
scheduler.add_job(adaptive_strategy_job, "interval", hours=6)


def start_scheduler():
    scheduler.start()


from telegram import Update
from telegram.ext import ContextTypes


async def usercount_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = db.get_db_connection()
    cursor = conn.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    await update.message.reply_text(f"Total users: {count}")
