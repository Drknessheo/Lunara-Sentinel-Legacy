
"""
Handles user account-related interactions with Binance.
"""

import asyncio
import logging

from binance.client import Client
from binance.exceptions import BinanceAPIException

# Using relative imports
from .market_data import get_current_price
from ... import db as new_db

logger = logging.getLogger(__name__)

class TradeError(Exception):
    """Generic trade-related error for Binance operations."""

def _blocking_get_all_spot_balances(user_id: int) -> list:
    api_key, secret_key = new_db.get_user_api_keys(user_id)
    if not api_key or not secret_key:
        raise TradeError("API keys not set. Use /setapi.")
    try:
        user_client = Client(api_key, secret_key)
        account_info = user_client.get_account()
        return [bal for bal in account_info["balances"] if float(bal["free"]) > 0 or float(bal["locked"]) > 0]
    except BinanceAPIException as e:
        raise TradeError(f"Binance API error: {e.message}")
    except Exception as e:
        raise TradeError(f"Unexpected error fetching balances: {e}")

async def get_all_spot_balances(user_id: int) -> list | None:
    """Asynchronously fetches all spot balances for a user."""
    try:
        return await asyncio.to_thread(_blocking_get_all_spot_balances, user_id)
    except TradeError as e:
        raise e  # Re-raise known trade errors
    except Exception as e:
        logger.error(f"Failed to fetch balances for user {user_id} in background thread: {e}")
        return None

async def get_total_account_balance_usdt(user_id: int) -> float:
    """
    Asynchronously calculates the total account value in USDT by fetching all balances
    and converting non-USDT assets to their USDT value.
    """
    try:
        balances = await get_all_spot_balances(user_id)
        if not balances:
            return 0.0

        total_usdt_value = 0.0
        stablecoins = {'USDT', 'BUSD', 'USDC', 'DAI', 'TUSD'}

        for balance in balances:
            asset = balance['asset']
            total_qty = float(balance['free']) + float(balance['locked'])

            if total_qty == 0:
                continue

            if asset in stablecoins:
                total_usdt_value += total_qty
            else:
                try:
                    symbol = f"{asset}USDT"
                    price = await get_current_price(symbol)
                    total_usdt_value += total_qty * price
                except Exception:
                    logger.warning(f"Could not get USDT price for asset '{asset}'. It will be excluded from total balance.")
        
        return total_usdt_value

    except TradeError as e:
        logger.error(f"Cannot calculate total balance for user {user_id} due to a trade error: {e}")
        raise  # Re-raise to be handled by the caller
    except Exception as e:
        logger.error(f"An unexpected error occurred while calculating total balance for user {user_id}: {e}")
        return 0.0
