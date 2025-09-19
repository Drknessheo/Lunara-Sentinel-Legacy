
# blueprint/lunessasignels/lunara-bot/src/db.py
"""
Thread-safe SQLite access layer for Lunessa / Lunara Bot.
"""

import sqlite3
import threading
import logging
from pathlib import Path
from . import config
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

# === Configuration ===
DB_PATH = Path(__file__).parent / "lunessa.db"
ENCRYPTION_KEY = getattr(config, 'SLIP_ENCRYPTION_KEY', None)
if not ENCRYPTION_KEY:
    raise ValueError("SLIP_ENCRYPTION_KEY is not set in the configuration.")
fernet = Fernet(ENCRYPTION_KEY.encode())

# Thread-local storage for per-thread connection
_thread_local = threading.local()


def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection for the current thread."""
    conn = getattr(_thread_local, "connection", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        _thread_local.connection = conn
    return conn


def close_connection():
    """Close the connection for the current thread."""
    conn = getattr(_thread_local, "connection", None)
    if conn:
        conn.close()
        _thread_local.connection = None


def _migrate_db(conn: sqlite3.Connection):
    """Applies database schema migrations to ensure compatibility."""
    cursor = conn.cursor()
    logger.info("Checking for necessary database migrations...")

    # Migration 1: Add 'watchlist' column to 'users' table
    cursor.execute("PRAGMA table_info(users)")
    columns = [row['name'] for row in cursor.fetchall()]

    if 'watchlist' not in columns:
        try:
            logger.info("Applying migration: Adding 'watchlist' column to 'users' table.")
            cursor.execute("ALTER TABLE users ADD COLUMN watchlist TEXT")
            logger.info("Migration successful.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                logger.warning("Migration for 'watchlist' column already applied by another process.")
            else:
                logger.error(f"Failed to apply 'watchlist' migration: {e}")
                raise
    else:
        logger.info("'watchlist' column already exists. No migration needed.")

    # Migration 2: Add 'stop_loss' column to 'trades' table
    cursor.execute("PRAGMA table_info(trades)")
    columns = [row['name'] for row in cursor.fetchall()]
    if 'stop_loss' not in columns:
        try:
            logger.info("Applying migration: Adding 'stop_loss' column to 'trades' table.")
            cursor.execute("ALTER TABLE trades ADD COLUMN stop_loss REAL")
            logger.info("Migration successful.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                logger.warning("Migration for 'stop_loss' column already applied by another process.")
            else:
                logger.error(f"Failed to apply 'stop_loss' migration: {e}")
                raise
    else:
        logger.info("'stop_loss' column already exists. No migration needed.")


# === Main DB Functions ===

def init_db():
    """Initializes the database, creates tables if they don't exist, and runs migrations."""
    conn = get_connection()
    with conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            api_key BLOB,
            secret_key BLOB,
            trading_mode TEXT DEFAULT 'PAPER',
            paper_balance REAL DEFAULT 10000.0,
            autotrade_enabled INTEGER DEFAULT 0,
            custom_rsi_buy REAL,
            custom_rsi_sell REAL,
            custom_stop_loss REAL,
            custom_trailing_activation REAL,
            custom_trailing_drop REAL,
            custom_profit_target REAL
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            buy_price REAL NOT NULL,
            quantity REAL NOT NULL,
            buy_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'open',
            stop_loss REAL
        );
        """)
        # Apply migrations to ensure older databases are up-to-date
        _migrate_db(conn)


def get_or_create_user(user_id):
    conn = get_connection()
    user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    created = False
    if not user:
        with conn:
            conn.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        created = True

    # Emperor's Decree: Pre-populate the admin's watchlist on first creation
    if created and user_id == config.ADMIN_USER_ID:
        logger.info(f"First-time setup for Admin user {user_id}. Adding default watchlist.")
        add_coins_to_watchlist(user_id, ['BTCUSDT', 'ETHUSDT', 'BNBUSDT'])

    return user, created


def update_trade(trade: dict):
    """Updates a trade in the database, specifically the stop_loss."""
    if 'id' not in trade or 'stop_loss' not in trade:
        logger.error("Attempted to update a trade without 'id' or 'stop_loss'.")
        return

    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE trades SET stop_loss = ? WHERE id = ?",
            (trade['stop_loss'], trade['id'])
        )
    logger.info(f"Updated trade {trade['id']} with new stop_loss: {trade['stop_loss']}")


def store_user_api_keys(user_id, api_key, secret_key):
    get_or_create_user(user_id)
    encrypted_api = fernet.encrypt(api_key.encode())
    encrypted_secret = fernet.encrypt(secret_key.encode())
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE users SET api_key=?, secret_key=? WHERE user_id=?",
            (encrypted_api, encrypted_secret, user_id)
        )


def get_user_api_keys(user_id):
    if user_id == config.ADMIN_USER_ID:
        return config.BINANCE_API_KEY, config.BINANCE_SECRET_KEY

    user, _ = get_or_create_user(user_id)
    if not user['api_key'] or not user['secret_key']:
        return None, None
    
    try:
        api_key = fernet.decrypt(user['api_key']).decode()
        secret_key = fernet.decrypt(user['secret_key']).decode()
        return api_key, secret_key
    except Exception:
        return None, None


def get_open_trades_by_user(user_id):
    conn = get_connection()
    return conn.execute("SELECT * FROM trades WHERE user_id=? AND status='open'", (user_id,)).fetchall()


def find_open_trade_by_id(trade_id, user_id):
    conn = get_connection()
    return conn.execute("SELECT * FROM trades WHERE id=? AND user_id=? AND status='open'",
                        (trade_id, user_id)).fetchone()


def mark_trade_closed(trade_id, reason="closed"):
    conn = get_connection()
    with conn:
        conn.execute("UPDATE trades SET status=? WHERE id=?", (reason, trade_id))


def get_user_trading_mode_and_balance(user_id):
    user, _ = get_or_create_user(user_id)
    return user['trading_mode'], user['paper_balance']


def get_user_count():
    conn = get_connection()
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def get_active_autotrade_count():
    conn = get_connection()
    return conn.execute("SELECT COUNT(*) FROM users WHERE autotrade_enabled=1").fetchone()[0]


def get_users_with_autotrade_enabled():
    # This function is essential for the TradeExecutor's main loop.
    conn = get_connection()
    users = conn.execute("SELECT user_id FROM users WHERE autotrade_enabled=1").fetchall()
    return [user['user_id'] for user in users]


def get_all_users():
    conn = get_connection()
    users = conn.execute("SELECT user_id FROM users").fetchall()
    return [user['user_id'] for user in users]


def add_coins_to_watchlist(user_id, coins_to_add: list):
    user, _ = get_or_create_user(user_id)
    # This check is important to avoid errors on first creation before the column exists pre-migration
    current_watchlist_str = user['watchlist'] if 'watchlist' in user.keys() else ''
    current_watchlist_str = current_watchlist_str or ''
    current_watchlist = set(current_watchlist_str.split(',')) if current_watchlist_str else set()
    for coin in coins_to_add:
        current_watchlist.add(coin.upper())
    new_watchlist_str = ','.join(sorted(list(current_watchlist)))
    conn = get_connection()
    with conn:
        conn.execute("UPDATE users SET watchlist=? WHERE user_id=?", (new_watchlist_str, user_id))


def remove_coins_from_watchlist(user_id, coins_to_remove: list):
    user, _ = get_or_create_user(user_id)
    current_watchlist_str = user['watchlist'] if 'watchlist' in user.keys() else ''
    current_watchlist_str = current_watchlist_str or ''
    current_watchlist = set(current_watchlist_str.split(',')) if current_watchlist_str else set()

    # Remove the specified coins
    for coin in coins_to_remove:
        current_watchlist.discard(coin.upper())  # Use discard to avoid errors if coin not in set

    new_watchlist_str = ','.join(sorted(list(current_watchlist)))
    conn = get_connection()
    with conn:
        conn.execute("UPDATE users SET watchlist=? WHERE user_id=?", (new_watchlist_str, user_id))


SETTING_TO_COLUMN_MAP = {
    'rsi_buy': 'custom_rsi_buy', 'rsi_sell': 'custom_rsi_sell', 'stop_loss': 'custom_stop_loss',
    'trailing_activation': 'custom_trailing_activation', 'trailing_drop': 'custom_trailing_drop',
    'profit_target': 'custom_profit_target', 'autotrade': 'autotrade_enabled',
    'trading_mode': 'trading_mode', 'paper_balance': 'paper_balance',
    'watchlist': 'watchlist'
}


def get_user_effective_settings(user_id: int) -> dict:
    user, _ = get_or_create_user(user_id)
    settings = {}
    for setting_name, column_name in SETTING_TO_COLUMN_MAP.items():
        if column_name not in user.keys():
            logger.warning(
                f"Column '{column_name}' not found for user {user_id}. Returning empty string. DB might be migrating.")
            value = ""
        else:
            value = user[column_name]

        if column_name == 'autotrade_enabled':
            settings[setting_name] = 'on' if value == 1 else 'off'
        elif column_name == 'watchlist':
            settings[setting_name] = value if value else ""
        else:
            settings[setting_name] = value if value is not None else 'Not Set'
    return settings


def update_user_setting(user_id: int, setting_name: str, value):
    if setting_name not in SETTING_TO_COLUMN_MAP:
        raise ValueError(f"Invalid setting name: {setting_name}")

    column_name = SETTING_TO_COLUMN_MAP[setting_name]
    processed_value = value
    if setting_name == 'autotrade':
        processed_value = 1 if str(value).lower() in ['on', 'true', '1', 'enabled'] else 0
        logger.info(f"Updating autotrade for user {user_id} to {processed_value}")  # Added logging
    elif setting_name == 'trading_mode':
        processed_value = str(value).upper()
        if processed_value not in ['LIVE', 'PAPER']:
            raise ValueError("Trading mode must be LIVE or PAPER")
    elif setting_name in ['paper_balance', 'rsi_buy', 'rsi_sell', 'stop_loss', 'trailing_activation',
                          'trailing_drop', 'profit_target']:
        processed_value = float(.value)
    elif setting_name == 'watchlist':
        # No special processing needed for watchlist, it's a string
        processed_value = str(value)

    conn = get_connection()
    with conn:
        conn.execute(f"UPDATE users SET {column_name}=? WHERE user_id=?", (processed_value, user_id))

