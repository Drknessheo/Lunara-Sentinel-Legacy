import functools
import logging
import sqlite3

# --- CORRECTED IMPORTS ---
# Use relative imports for config to ensure a single, unified object.
from .. import config

# Restore the robust, multi-strategy import for decrypt_data to handle different execution contexts.
# This was the cause of the TypeError: 'NoneType' object is not callable.
decrypt_data = None
try:
    from ..security import decrypt_data as _dd
    decrypt_data = _dd
except (ImportError, ModuleNotFoundError):
    try:
        from security import decrypt_data as _dd_alt
        decrypt_data = _dd_alt
    except (ImportError, ModuleNotFoundError):
        try:
            import importlib
            _m = importlib.import_module("src.security")
            decrypt_data = getattr(_m, "decrypt_data", None)
        except Exception:
            decrypt_data = None

# Allow tests to override the database path
DB_PATH = "lunara_bot.db"

logger = logging.getLogger(__name__)


def db_connection(func):
    """Decorator to handle database connection and cursor management."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if DB_PATH == ":memory:":
            db_uri = "file::memory:?cache=shared"
            conn = sqlite3.connect(db_uri, uri=True, check_same_thread=False)
        else:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            result = func(cursor, *args, **kwargs)
            conn.commit()
            return result
        except sqlite3.Error as e:
            print(f"Database error in {func.__name__}: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()

    return wrapper

# (The rest of the file remains unchanged)
@db_connection
def add_coins_to_watchlist(cursor, user_id: int, coins: list[str]):
    """Adds a list of coins to a user's watchlist."""
    for coin in coins:
        try:
            cursor.execute(
                "INSERT INTO watchlist (user_id, coin_symbol) VALUES (?, ?)", (user_id, coin)
            )
        except sqlite3.IntegrityError:
            pass

SETTING_TO_COLUMN_MAP = {
    "rsi_buy": "custom_rsi_buy",
    "rsi_sell": "custom_rsi_sell",
    "stop_loss": "custom_stop_loss",
    "trailing_activation": "custom_trailing_activation",
    "trailing_drop": "custom_trailing_drop",
    "profit_target": "custom_profit_target",
}

CUSTOM_SETTINGS_MAPPING = {
    "custom_rsi_buy": "RSI_BUY_THRESHOLD",
    "custom_rsi_sell": "RSI_SELL_THRESHOLD",
    "custom_stop_loss": "STOP_LOSS_PERCENTAGE",
    "custom_trailing_activation": "TRAILING_PROFIT_ACTIVATION_PERCENT",
    "custom_trailing_drop": "TRAILING_STOP_DROP_PERCENT",
    "custom_profit_target": "PROFIT_TARGET_PERCENTAGE",
}


@db_connection
def update_user_setting(cursor, user_id: int, setting_name: str, value):
    """Updates a user's custom setting in the database. If value is None, resets to default."""
    column = SETTING_TO_COLUMN_MAP.get(setting_name)
    if not column:
        raise ValueError(f"Unknown setting: {setting_name}")
    cursor.execute(
        f"UPDATE users SET {column} = ? WHERE user_id = ?", (value, user_id)
    )


@db_connection
def update_user_subscription(
    cursor, user_id: int, tier: str = "PREMIUM", expires: str = None
):
    """Update a user's subscription tier and expiration."""
    _get_or_create_user(cursor, user_id)
    cursor.execute(
        "UPDATE users SET subscription_tier = ?, subscription_expires = ? WHERE user_id = ?",
        (tier, expires, user_id),
    )


@db_connection
def store_user_api_keys(cursor, user_id: int, api_key: str, secret_key: str):
    """Encrypt and store API keys for a user."""
    from cryptography.fernet import Fernet
    key = config.BINANCE_ENCRYPTION_KEY
    fernet = Fernet(key)
    encrypted_api = fernet.encrypt(api_key.encode())
    encrypted_secret = fernet.encrypt(secret_key.encode())
    _get_or_create_user(cursor, user_id)
    cursor.execute(
        "UPDATE users SET api_key = ?, secret_key = ? WHERE user_id = ?",
        (encrypted_api, encrypted_secret, user_id),
    )


@db_connection
def get_user_tier_db(cursor, user_id: int) -> str:
    """Decorator-wrapped version for bot usage."""
    return get_user_tier(cursor, user_id)


@db_connection
def get_or_create_user(cursor, user_id: int):
    """Public API: Creates or retrieves a user from the database."""
    return _get_or_create_user(cursor, user_id)


def _get_or_create_user(cursor, user_id: int):
    """Gets a user from the DB or creates a new one with default settings."""
    user = cursor.execute(
        "SELECT * FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not user:
        cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        user = cursor.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    return user


def get_user_tier(cursor, user_id: int) -> str:
    """Retrieves a user's subscription tier."""
    if user_id == getattr(config, "ADMIN_USER_ID", None):
        return "PREMIUM"
    user = _get_or_create_user(cursor, user_id)
    return user["subscription_tier"]


@db_connection
def get_user_subscription_db(cursor, user_id: int) -> tuple[str, str | None]:
    """Decorator-wrapped version for bot usage."""
    if user_id == getattr(config, "ADMIN_USER_ID", None):
        return "PREMIUM", None
    user = _get_or_create_user(cursor, user_id)
    return user["subscription_tier"], user["subscription_expires"]


@db_connection
def initialize_database(cursor):
    """Creates the tables if they don't exist."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            coin_symbol TEXT NOT NULL,
            buy_price REAL NOT NULL,
            buy_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            closed_at DATETIME,
            status TEXT NOT NULL,
            sell_price REAL,
            stop_loss_price REAL,
            take_profit_price REAL,
            peak_price REAL,
            mode TEXT DEFAULT 'LIVE',
            trade_size_usdt REAL,
            quantity REAL,
            close_reason TEXT,
            win_loss TEXT,
            pnl_percentage REAL,
            rsi_at_buy REAL,
            closed_by TEXT
        );
    """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            api_key BLOB,
            secret_key BLOB,
            subscription_tier TEXT DEFAULT 'FREE',
            subscription_expires DATETIME,
            custom_rsi_buy REAL,
            custom_rsi_sell REAL,
            custom_stop_loss REAL,
            custom_trailing_activation REAL,
            custom_trailing_drop REAL,
            custom_profit_target REAL,
            trading_mode TEXT DEFAULT 'LIVE',
            paper_balance REAL DEFAULT 10000.0,
            autotrade_enabled INTEGER DEFAULT NULL
        );
    """
    )

@db_connection
def get_user_api_keys(cursor, user_id: int):
    """Retrieves and decrypts a user's Binance API keys."""
    row = cursor.execute(
        "SELECT api_key, secret_key FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row or not row["api_key"] or not row["secret_key"]:
        return None, None
    if decrypt_data is None:
        logger.error("Decryption function not available. Cannot decrypt API keys.")
        return None, None
    api_key = decrypt_data(row["api_key"])
    secret_key = decrypt_data(row["secret_key"])
    return api_key, secret_key


@db_connection
def get_user_effective_settings(cursor, user_id: int) -> dict:
    """Returns the effective settings for a user."""
    tier = get_user_tier(cursor, user_id)
    settings = config.get_active_settings(tier).copy()
    user_data = _get_or_create_user(cursor, user_id)
    if not user_data:
        return settings
    for db_key, settings_key in CUSTOM_SETTINGS_MAPPING.items():
        if db_key in user_data.keys() and user_data[db_key] is not None:
            settings[settings_key] = user_data[db_key]
    return settings
