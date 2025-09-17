'''
# blueprint/lunessasignels/lunara-bot/src/db.py
"""
Thread-safe SQLite access layer for Lunessa / Lunara Bot.
Each thread gets its own connection; no more check_same_thread hacks.
"""

import sqlite3
import threading
from pathlib import Path
from . import config
from cryptography.fernet import Fernet

# === Configuration ===
DB_PATH = Path(__file__).parent / "lunessa.db"

# --- Encryption setup ---
# Ensure the key is in the correct format (bytes)
ENCRYPTION_KEY = config.BINANCE_ENCRYPTION_KEY.encode()
cipher_suite = Fernet(ENCRYPTION_KEY)


# Thread-local storage for per-thread connection
_thread_local = threading.local()

def get_connection() -> sqlite3.Connection:
    """
    Return a SQLite connection for the current thread.
    One connection per thread, automatically reopened if closed.
    """
    conn = getattr(_thread_local, "connection", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        _thread_local.connection = conn
    return conn

def close_connection():
    """
    Close the connection for the current thread (use at thread shutdown).
    """
    conn = getattr(_thread_local, "connection", None)
    if conn:
        conn.close()
        _thread_local.connection = None

# === Database operations below use get_connection() instead of a global conn ===

def init_db():
    conn = get_connection()
    with conn:
        conn.execute("""
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
    """)
        conn.execute("""
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
    """)

def get_user_count():
    """Returns the total number of users in the database."""
    conn = get_connection()
    with conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

def get_or_create_user(telegram_id):
    """
    Retrieves a user by their Telegram ID. If the user does not exist,
    a new record is created.
    Returns a tuple of (user_data, created_boolean).
    """
    conn = get_connection()
    str_telegram_id = str(telegram_id)
    
    user = conn.execute("SELECT * FROM users WHERE user_id=?", (str_telegram_id,)).fetchone()
    if user:
        return user, False

    try:
        with conn:
            cursor = conn.execute(
                "INSERT INTO users (user_id) VALUES (?)",
                (str_telegram_id,)
            )
            if cursor.rowcount > 0:
                new_user = conn.execute("SELECT * FROM users WHERE user_id=?", (str_telegram_id,)).fetchone()
                return new_user, True
    except sqlite3.IntegrityError:
        pass

    user = conn.execute("SELECT * FROM users WHERE user_id=?", (str_telegram_id,)).fetchone()
    return user, False

def fetch_user(telegram_id):
    conn = get_connection()
    return conn.execute("SELECT * FROM users WHERE user_id=?", (str(telegram_id),)).fetchone()

def insert_user(telegram_id, api_key, api_secret, settings):
    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO users (user_id, api_key, secret_key, settings) VALUES (?,?,?,?)",
            (str(telegram_id), api_key, api_secret, settings)
        )

def get_open_trades_by_user(user_id):
    conn = get_connection()
    return conn.execute("SELECT * FROM trades WHERE user_id=? AND status='open'", (user_id,)).fetchall()

def find_open_trade(trade_id_or_symbol, user_id):
    """
    Finds an open trade by its ID or symbol for a specific user.
    """
    conn = get_connection()
    if str(trade_id_or_symbol).isdigit():
        return conn.execute("SELECT * FROM trades WHERE id=? AND user_id=? AND status='open'", (int(trade_id_or_symbol), user_id)).fetchone()
    else:
        return conn.execute("SELECT * FROM trades WHERE symbol=? AND user_id=? AND status='open'", (str(trade_id_or_symbol).upper(), user_id)).fetchone()

def mark_trade_closed(trade_id, reason="closed"):
    conn = get_connection()
    with conn:
        conn.execute("UPDATE trades SET status=? WHERE id=?", (reason, trade_id))

def get_user_trading_mode_and_balance(user_id):
    """
    Retrieves the trading mode and paper balance for a user, creating the user if they don't exist.
    """
    user, _ = get_or_create_user(user_id)
    return user['trading_mode'], user['paper_balance']

def store_user_api_keys(user_id, api_key, secret_key):
    """Encrypts and stores user API keys."""
    user, _ = get_or_create_user(user_id)
    encrypted_api_key = cipher_suite.encrypt(api_key.encode())
    encrypted_secret_key = cipher_suite.encrypt(secret_key.encode())
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE users SET api_key=?, secret_key=? WHERE user_id=?",
            (encrypted_api_key, encrypted_secret_key, user_id)
        )

def get_user_api_keys(user_id):
    """Retrieves and decrypts user API keys."""
    user, _ = get_or_create_user(user_id)
    if not user['api_key'] or not user['secret_key']:
        return None, None
    
    decrypted_api_key = cipher_suite.decrypt(user['api_key']).decode()
    decrypted_secret_key = cipher_suite.decrypt(user['secret_key']).decode()
    return decrypted_api_key, decrypted_secret_key


SETTING_TO_COLUMN_MAP = {
    'rsi_buy': 'custom_rsi_buy',
    'rsi_sell': 'custom_rsi_sell',
    'stop_loss': 'custom_stop_loss',
    'trailing_activation': 'custom_trailing_activation',
    'trailing_drop': 'custom_trailing_drop',
    'profit_target': 'custom_profit_target',
    'autotrade': 'autotrade_enabled'
}

def get_user_effective_settings(user_id: int) -> dict:
    """
    Retrieves all trade-related settings for a user, creating the user if they don't exist.
    Returns a dictionary of settings with user-friendly keys.
    """
    user, _ = get_or_create_user(user_id)
    
    column_to_setting_map = {v: k for k, v in SETTING_TO_COLUMN_MAP.items()}
    column_to_setting_map['trading_mode'] = 'trading_mode'

    settings = {}
    for column, setting_name in column_to_setting_map.items():
        if column in user.keys():
            value = user[column]
            if column == 'autotrade_enabled':
                if value is None:
                    value = 'Disabled'
                else:
                    value = 'Enabled' if value == 1 else 'Disabled'
            settings[setting_name] = value
            
    return settings

def update_user_setting(user_id: int, setting_name: str, value):
    """
    Updates a specific setting for a user in the database.
    """
    if setting_name not in SETTING_TO_COLUMN_MAP:
        raise ValueError(f"Invalid setting name: {setting_name}")

    column_name = SETTING_TO_COLUMN_MAP[setting_name]
    
    get_or_create_user(user_id)

    if setting_name == 'autotrade':
        processed_value = 1 if str(value).lower() in ['on', 'true', '1', 'enabled'] else 0
    else:
        try:
            processed_value = float(value)
        except ValueError:
            raise TypeError(f"Invalid value type for {setting_name}. Expected a number.")

    conn = get_connection()
    with conn:
        conn.execute(
            f"UPDATE users SET {column_name}=? WHERE user_id=?",
            (processed_value, user_id)
        )
''