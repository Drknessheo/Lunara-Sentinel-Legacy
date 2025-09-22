
"""
Handles fetching market data from Binance.
"""

import asyncio
import logging
from functools import lru_cache

from binance.exceptions import BinanceAPIException
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Using relative imports
from .client import ensure_binance_client, client

logger = logging.getLogger(__name__)

class TradeError(Exception):
    """Generic trade-related error for Binance operations."""

@lru_cache(maxsize=128)
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(BinanceAPIException))
def get_symbol_info(symbol: str):
    """
    Fetches and caches trading rules for a symbol, like precision.
    Returns a dictionary with symbol information or None on error.
    """
    try:
        ensure_binance_client()
        if not client:
            return None
        return client.get_symbol_info(symbol)
    except BinanceAPIException as e:
        logger.error(f"Could not fetch symbol info for {symbol}: {e}")
        return None

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(BinanceAPIException))
def _blocking_get_historical_klines(*args, **kwargs):
    """Wrapper for client.get_historical_klines with retry logic."""
    ensure_binance_client()
    if not client:
        raise TradeError("Binance client not available for fetching klines.")
    return client.get_historical_klines(*args, **kwargs)

async def get_historical_klines(*args, **kwargs):
    """Asynchronous version of get_historical_klines."""
    return await asyncio.to_thread(_blocking_get_historical_klines, *args, **kwargs)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(BinanceAPIException))
def _blocking_get_current_price(symbol: str):
    """Synchronous implementation for fetching the current price."""
    ensure_binance_client()
    if not client:
        raise TradeError("Binance client not available for fetching price.")
    ticker = client.get_symbol_ticker(symbol=symbol)
    return float(ticker["price"])

async def get_current_price(symbol: str):
    """Asynchronously fetches the current price of a given symbol from Binance."""
    return await asyncio.to_thread(_blocking_get_current_price, symbol)
