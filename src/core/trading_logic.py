
"""
This module contains the core trading logic for the Lunara bot.
...
"""

import logging
import asyncio
import time
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from telegram.ext import ContextTypes
from binance.exceptions import BinanceAPIException
from binance.client import Client

# CORRECTED: Using relative imports
from . import binance_client
from .binance_client import TradeError
from .. import config
from ..indicators import calculate_rsi, calc_atr
from ..modules import db_access as db
from ..risk_management import get_atr_stop, should_pause_trading
from ..memory import log_trade_outcome
from ..trade_guard import TradeValidator
from . import gemini_cacher

logger = logging.getLogger(__name__)

# Emperor's Decree: A platoon must have a minimum strength.
MIN_TRADE_USDT = 11.0  # 10 USDT base + 10% buffer for dust and fees.

def get_monitored_coins():
    """Returns the configured list of monitored coins."""
    return getattr(config, "AI_MONITOR_COINS", [])

# --- Indicator Calculations ---
# ... (existing indicator functions) ...

# --- Trade Execution Logic ---

async def place_buy_order_logic(user_id: int, symbol: str, usdt_amount: float):
    """Handles the logic for placing a buy order, enforcing minimum trade size."""
    user_client = binance_client.get_user_client(user_id)
    if not user_client:
        raise TradeError("Binance client is not available for this user.")

    info = await binance_client.get_symbol_info(symbol, user_id=user_id)
    if not info:
        raise TradeError(f"Could not retrieve trading rules for {symbol}.")

    # Determine the effective minimum trade size
    binance_min_notional = float([f["minNotional"] for f in info["filters"] if f["filterType"] == "NOTIONAL"][0])
    effective_min = max(MIN_TRADE_USDT, binance_min_notional)

    # Enforce the platoon strategy
    if usdt_amount < effective_min:
        raise TradeError(
            f"Order value of {usdt_amount:.2f} USDT is below the required minimum of "
            f"${effective_min:.2f} for {symbol}."
        )

    try:
        logger.info(f"Placing MARKET BUY for {usdt_amount:.2f} USDT of {symbol} for user {user_id}")
        # Use the async client's method
        order = await user_client.create_order(
            symbol=symbol,
            side=Client.SIDE_BUY,
            type=Client.ORDER_TYPE_MARKET,
            quote_order_qty=usdt_amount
        )
        
        # Ensure fills are present and extract data
        if order and order.get('fills'):
            entry_price = float(order['fills'][0]['price'])
            quantity = float(order['executed_qty'])
            # Return a dictionary for consistency
            return {'success': True, 'order': order, 'price': entry_price, 'quantity': quantity}
        else:
            # Handle cases where the order might be created but not filled immediately
            # or the response format is unexpected.
            logger.warning(f"BUY order for {symbol} created but no fills info returned immediately. Order: {order}")
            # We might need to query the order status separately here. For now, assume failure if no fills.
            raise TradeError("Order created but fill information was not returned.")

    except BinanceAPIException as e:
        logger.error(f"LIVE BUY order failed for {symbol}: {e}")
        raise TradeError(f"Binance API Error on Buy: {e.message}")
    except Exception as e:
        logger.error(f"An unexpected error occurred during BUY for {symbol}: {e}")
        raise TradeError(f"An unexpected error occurred: {e}")


async def place_sell_order_logic(user_id: int, symbol: str, quantity: float):
    """Handles the logic for placing a market sell order."""
    user_client = binance_client.get_user_client(user_id)
    if not user_client:
        raise TradeError("Binance client is not available for this user.")

    info = await binance_client.get_symbol_info(symbol, user_id=user_id)
    if not info:
        raise TradeError(f"Could not retrieve trading rules for {symbol}.")

    # Apply quantity precision filter
    step_size = None
    for f in info['filters']:
        if f['filterType'] == 'LOT_SIZE':
            step_size = float(f['stepSize'])
            break
            
    if step_size:
        precision = int(round(-np.log10(step_size)))
        quantity = round(quantity, precision)
    
    # Check minNotional before selling the specified quantity
    current_price = await binance_client.get_current_price(symbol, user_id=user_id)
    notional_value = quantity * current_price
    min_notional = float([f["minNotional"] for f in info["filters"] if f["filterType"] == "NOTIONAL"][0])

    if notional_value < min_notional:
        logger.warning(f"SELL notional value ({notional_value}) for {symbol} is below minimum ({min_notional}). Cannot sell.")
        # In some cases, we might want to force sell the dust. For now, we just log and skip.
        raise TradeError(f"Sell value is below the minimum of ${min_notional:.2f}.")

    try:
        logger.info(f"Placing MARKET SELL for {quantity} of {symbol} for user {user_id}")
        order = await user_client.create_order(
            symbol=symbol,
            side=Client.SIDE_SELL,
            type=Client.ORDER_TYPE_MARKET,
            quantity=quantity
        )
        
        if order and order.get('fills'):
            sell_price = float(order['fills'][0]['price'])
            # Return a dictionary for consistency
            return {'success': True, 'order': order, 'price': sell_price}
        else:
            logger.warning(f"SELL order for {symbol} created but no fills info returned. Order: {order}")
            raise TradeError("Order created but fill information was not returned.")

    except BinanceAPIException as e:
        logger.error(f"LIVE SELL order failed for {symbol}: {e}")
        raise TradeError(f"Binance API Error on Sell: {e.message}")
    except Exception as e:
        logger.error(f"An unexpected error occurred during SELL for {symbol}: {e}")
        raise TradeError(f"An unexpected error occurred during sell: {e}")


# --- Core Monitoring and Trading Cycle ---
# ... (rest of the file remains the same) ...
