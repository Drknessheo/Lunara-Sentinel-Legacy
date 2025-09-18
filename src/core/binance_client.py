"""
Handles all direct communication with the Binance API.

This module centralizes Binance client management, API calls for market data,
and order execution. It is designed to be self-contained and easily testable.
"""

import logging
import os
import math

import requests
from binance.client import Client
from binance.exceptions import BinanceAPIException
from requests.adapters import HTTPAdapter
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from urllib3.util.retry import Retry
from functools import lru_cache

# CORRECTED: Using relative imports
from .. import config
from .. import db as new_db # Use the new thread-safe db module

logger = logging.getLogger(__name__)

# --- Custom Exception ---
class TradeError(Exception):
    """Generic trade-related error for Binance operations."""

# --- Module-level Binance Client Management ---

# Lazy-initialized global client and status variables
BINANCE_AVAILABLE = False
BINANCE_INIT_ERROR = None
client: Client | None = None

def _build_session(timeout: int = 10, max_retries: int = 3) -> requests.Session:
    """Builds a requests.Session with retry logic for robust HTTP requests."""
    session = requests.Session()
    retries = Retry(
        total=max_retries,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST", "PUT", "DELETE", "HEAD"),
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def ensure_binance_client() -> None:
    """
    Ensures the module-level `client` is initialized.

    Safe to call repeatedly. It will not raise on Binance errors but will set
    `BINANCE_AVAILABLE` and `BINANCE_INIT_ERROR` for callers to check.
    """
    global client, BINANCE_AVAILABLE, BINANCE_INIT_ERROR

    if client and BINANCE_AVAILABLE:
        return

    api_key = getattr(config, "BINANCE_API_KEY", None) or os.getenv("BINANCE_API_KEY")
    secret_key = getattr(config, "BINANCE_SECRET_KEY", None) or os.getenv("BINANCE_SECRET_KEY")

    if not (api_key and secret_key):
        BINANCE_AVAILABLE = False
        BINANCE_INIT_ERROR = "API keys not configured"
        client = None
        logger.warning("Binance API keys not found. Trading functions will be disabled.")
        return

    try:
        session = _build_session()
        created_client = Client(api_key, secret_key, requests_params={"timeout": 10})
        if hasattr(created_client, "session"):
            created_client.session = session
        
        created_client.ping() # Health check
        
        client = created_client
        BINANCE_AVAILABLE = True
        BINANCE_INIT_ERROR = None
        logger.info("Binance client initialized successfully.")

    except BinanceAPIException as be:
        client = None
        BINANCE_AVAILABLE = False
        BINANCE_INIT_ERROR = repr(be)
        if "restricted location" in str(be).lower() or "451" in str(be):
            logger.warning("Binance API unavailable due to restricted location (451).")
        else:
            logger.exception("Failed to initialize Binance client due to API error.")
    except Exception as e:
        client = None
        BINANCE_AVAILABLE = False
        BINANCE_INIT_ERROR = repr(e)
        logger.exception("An unexpected error occurred during Binance client initialization.")


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
def get_historical_klines(*args, **kwargs):
    """
    Wrapper for client.get_historical_klines with retry logic."""
    ensure_binance_client()
    if not client:
        raise TradeError("Binance client not available for fetching klines.")
    return client.get_historical_klines(*args, **kwargs)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(BinanceAPIException))
def get_current_price(symbol: str):
    """
    Fetches the current price of a given symbol from Binance."""
    ensure_binance_client()
    if not client:
        raise TradeError("Binance client not available for fetching price.")
    ticker = client.get_symbol_ticker(symbol=symbol)
    return float(ticker["price"])

def get_all_spot_balances(user_id: int) -> list | None:
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
