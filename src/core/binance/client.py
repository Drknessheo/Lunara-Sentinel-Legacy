
"""
Manages the core Binance client connection.
"""

import logging
import os
import requests
from binance.client import Client
from binance.exceptions import BinanceAPIException
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Using relative imports
from ... import config

logger = logging.getLogger(__name__)

# Module-level client and status variables
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
