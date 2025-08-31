import sqlite3
import functools
import logging
import config
from security import decrypt_data

logger = logging.getLogger(__name__)

def db_connection(func):
    """Decorator to handle database connection and cursor management."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        conn = sqlite3.connect('lunara_bot.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            result = func(cursor, *args, **kwargs)
            conn.commit()
            return result
        except sqlite3.Error as e:
            print(f"Database error in {func.__name__}: {e}")
            conn.rollback()
            raise  # Re-raise the exception after rollback
        finally:
            conn.close()
    return wrapper

SETTING_TO_COLUMN_MAP = {
    'rsi_buy': 'custom_rsi_buy',
    'rsi_sell': 'custom_rsi_sell',
    'stop_loss': 'custom_stop_loss',
    'trailing_activation': 'custom_trailing_activation',
    'trailing_drop': 'custom_trailing_drop',
}

CUSTOM_SETTINGS_MAPPING = {
    'custom_rsi_buy': 'RSI_BUY_THRESHOLD',
    'custom_rsi_sell': 'RSI_SELL_THRESHOLD',
    'custom_stop_loss': 'STOP_LOSS_PERCENTAGE',
    'custom_trailing_activation': 'TRAILING_PROFIT_ACTIVATION_PERCENT',
    'custom_trailing_drop': 'TRAILING_STOP_DROP_PERCENT',
}

@db_connection
def update_user_setting(cursor, user_id: int, setting_name: str, value):
    """
    Updates a user's custom setting in the database. If value is None, resets to default.
    """
    column = SETTING_TO_COLUMN_MAP.get(setting_name)
    if not column:
        raise ValueError(f"Unknown setting: {setting_name}")
    if value is None:
        cursor.execute(f"UPDATE users SET {column} = NULL WHERE user_id = ?", (user_id,))
    else:
        cursor.execute(f"UPDATE users SET {column} = ? WHERE user_id = ?", (value, user_id))

def update_user_subscription(cursor, user_id: int, tier: str = "PREMIUM", expires: str = None):
    """Update a user's subscription tier and expiration."""
    get_or_create_user(cursor, user_id)
    cursor.execute("UPDATE users SET subscription_tier = ?, subscription_expires = ? WHERE user_id = ?", (tier, expires, user_id))

@db_connection
def store_user_api_keys(cursor, user_id: int, api_key: str, secret_key: str):
    """Encrypt and store API keys for a user."""
    from cryptography.fernet import Fernet
    key = config.BINANCE_ENCRYPTION_KEY
    fernet = Fernet(key)
    encrypted_api = fernet.encrypt(api_key.encode())
    encrypted_secret = fernet.encrypt(secret_key.encode())
    get_or_create_user(cursor, user_id)
    cursor.execute("UPDATE users SET api_key = ?, secret_key = ? WHERE user_id = ?", (encrypted_api, encrypted_secret, user_id))

@db_connection
def get_user_tier_db(cursor, user_id: int) -> str:
    """Decorator-wrapped version for bot usage."""
    return get_user_tier(cursor, user_id)

@db_connection
def get_or_create_user_db(cursor, user_id: int):
    """Decorator-wrapped version for bot usage."""
    return get_or_create_user(cursor, user_id)

def get_or_create_user(cursor, user_id: int):
    """Gets a user from the DB or creates a new one with default settings."""
    user = cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not user:
        cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        user = cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return user

def get_user_tier(cursor, user_id: int) -> str:
    """
    Retrieves a user's subscription tier.
    Treats the admin/creator as 'PREMIUM' for all commands.
    """
    if user_id == getattr(config, 'ADMIN_USER_ID', None):
        return 'PREMIUM'
    user = get_or_create_user(cursor, user_id)
    return user['subscription_tier']

def get_user_subscription(cursor, user_id: int) -> tuple[str, str | None]:
    """
    Retrieves a user's subscription tier and expiration date.
    Treats the admin/creator as 'PREMIUM' with no expiration.
    """
    if user_id == getattr(config, 'ADMIN_USER_ID', None):
        return 'PREMIUM', None
    user = get_or_create_user(cursor, user_id)
    return user['subscription_tier'], user['subscription_expires']

@db_connection
def get_user_subscription_db(cursor, user_id: int) -> tuple[str, str | None]:
    """Decorator-wrapped version for bot usage."""
    return get_user_subscription(cursor, user_id)

@db_connection
def initialize_database(cursor):
    """Creates the tables if they don't exist."""
    cursor.execute("""
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS coin_performance (
            coin_symbol TEXT PRIMARY KEY,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            total_pnl_percentage REAL DEFAULT 0.0
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            coin_symbol TEXT NOT NULL,
            add_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, coin_symbol)
        );
    """)
    cursor.execute("""
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
            trading_mode TEXT DEFAULT 'LIVE',
            paper_balance REAL DEFAULT 10000.0,
            autotrade_enabled INTEGER DEFAULT NULL
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS premium_users (
            user_id INTEGER PRIMARY KEY
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS autotrades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            buy_price REAL NOT NULL,
            sell_price REAL,
            status TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            closed_at DATETIME
        );
    """)

@db_connection
def migrate_schema(cursor):
    """
    Checks the database schema and applies any necessary migrations,
    such as adding new columns to existing tables.
    """
    cursor.execute("PRAGMA table_info(trades)")
    trade_columns = [info[1] for info in cursor.fetchall()]

    if 'peak_price' not in trade_columns:
        cursor.execute("ALTER TABLE trades ADD COLUMN peak_price REAL")
    if 'closed_at' not in trade_columns:
        cursor.execute("ALTER TABLE trades ADD COLUMN closed_at DATETIME")
    if 'mode' not in trade_columns:
        cursor.execute("ALTER TABLE trades ADD COLUMN mode TEXT DEFAULT 'LIVE'")
        cursor.execute("ALTER TABLE trades ADD COLUMN trade_size_usdt REAL")
    if 'quantity' not in trade_columns:
        cursor.execute("ALTER TABLE trades ADD COLUMN quantity REAL")
    if 'close_reason' not in trade_columns:
        cursor.execute("ALTER TABLE trades ADD COLUMN close_reason TEXT")
    if 'win_loss' not in trade_columns:
        cursor.execute("ALTER TABLE trades ADD COLUMN win_loss TEXT")
        cursor.execute("ALTER TABLE trades ADD COLUMN pnl_percentage REAL")
    if 'dsl_mode' not in trade_columns:
        cursor.execute("ALTER TABLE trades ADD COLUMN dsl_mode TEXT")
        cursor.execute("ALTER TABLE trades ADD COLUMN current_dsl_stage INTEGER DEFAULT 0")
    if 'closed_by' not in trade_columns:
        cursor.execute("ALTER TABLE trades ADD COLUMN closed_by TEXT")

    cursor.execute("PRAGMA table_info(users)")
    user_columns = [info[1] for info in cursor.fetchall()]

    if 'trading_mode' not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN trading_mode TEXT DEFAULT 'LIVE'")
        cursor.execute("ALTER TABLE users ADD COLUMN paper_balance REAL DEFAULT 10000.0")
    if 'custom_stop_loss' not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN custom_rsi_buy REAL")
        cursor.execute("ALTER TABLE users ADD COLUMN custom_rsi_sell REAL")
        cursor.execute("ALTER TABLE users ADD COLUMN custom_stop_loss REAL")
        cursor.execute("ALTER TABLE users ADD COLUMN custom_trailing_activation REAL")
        cursor.execute("ALTER TABLE users ADD COLUMN custom_trailing_drop REAL")

@db_connection
def get_all_user_ids(cursor):
    """Returns a list of all user IDs in the users table."""
    rows = cursor.execute("SELECT user_id FROM users").fetchall()
    return [row['user_id'] for row in rows]


@db_connection
def get_estimated_audit_rows(cursor, limit: int = 10, offset: int = 0):
    """Return rows from estimated_quantities_audit for preview in the bot."""
    # Ensure table exists
    cursor.execute('''CREATE TABLE IF NOT EXISTS estimated_quantities_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER NOT NULL,
        estimated_quantity REAL NOT NULL,
        source_price REAL,
        source_trade_size_usdt REAL,
        confidence REAL,
        promoted INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    rows = cursor.execute('SELECT * FROM estimated_quantities_audit ORDER BY created_at DESC LIMIT ? OFFSET ?', (limit, offset)).fetchall()
    return rows


@db_connection
def promote_estimate_to_trade(cursor, audit_id: int) -> tuple[bool, str]:
    """Atomically promote an estimate into trades.quantity and mark audit row as promoted.
    Returns (success, message)."""
    audit_row = cursor.execute('SELECT * FROM estimated_quantities_audit WHERE id = ?', (audit_id,)).fetchone()
    if not audit_row:
        return False, 'Audit row not found'
    if audit_row['promoted']:
        return False, 'Already promoted'
    trade_id = audit_row['trade_id']
    est_qty = audit_row['estimated_quantity']
    trade_row = cursor.execute('SELECT quantity FROM trades WHERE id = ?', (trade_id,)).fetchone()
    if not trade_row:
        return False, 'Trade not found'
    if trade_row['quantity'] is not None and trade_row['quantity'] > 0:
        return False, 'Trade already has quantity'
    cursor.execute('UPDATE trades SET quantity = ? WHERE id = ?', (est_qty, trade_id))
    cursor.execute('UPDATE estimated_quantities_audit SET promoted = 1 WHERE id = ?', (audit_id,))
    return True, f'Promoted estimate {audit_id} -> trade {trade_id}'

@db_connection
def get_trade_by_id(cursor, trade_id: int, user_id: int):
    """Fetches a trade by its ID and user ID."""
    trade = cursor.execute("SELECT * FROM trades WHERE id = ? AND user_id = ?", (trade_id, user_id)).fetchone()
    return trade

@db_connection
def get_autotrade_status(cursor, user_id: int):
    row = cursor.execute("SELECT autotrade_enabled FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return bool(row['autotrade_enabled']) if row and row['autotrade_enabled'] is not None else False

@db_connection
def set_autotrade_status(cursor, user_id: int, enabled: bool):
    """Set autotrade status for a user in the users table."""
    get_or_create_user(cursor, user_id)  # Ensures user exists
    cursor.execute("UPDATE users SET autotrade_enabled = ? WHERE user_id = ?", (int(enabled), user_id))

@db_connection
def get_open_trades(cursor, user_id: int):
    """Retrieves all open trades for a specific user."""
    cursor.execute(
        "SELECT id, user_id, coin_symbol, buy_price, buy_timestamp, stop_loss_price, take_profit_price FROM trades WHERE user_id = ? AND status = 'open'", (user_id,)
    )
    return cursor.fetchall()

@db_connection
def get_user_trading_mode_and_balance(cursor, user_id: int):
    """Gets the user's trading mode and paper balance."""
    user = get_or_create_user(cursor, user_id)
    return user['trading_mode'], user['paper_balance']

@db_connection
def get_watched_items_by_user(cursor, user_id: int):
    """Retrieves all watched symbols for a specific user."""
    items = cursor.execute(
        "SELECT coin_symbol, add_timestamp FROM watchlist WHERE user_id = ?", (user_id,)
    ).fetchall()
    return items

@db_connection
def get_user_api_keys(cursor, user_id: int):
    """
    Retrieves and decrypts a user's Binance API keys.
    """
    row = cursor.execute("SELECT api_key, secret_key FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not row or not row['api_key'] or not row['secret_key']:
        return None, None
    api_key = decrypt_data(row['api_key'])
    secret_key = decrypt_data(row['secret_key'])
    return api_key, secret_key

@db_connection
def get_user_effective_settings(cursor, user_id: int) -> dict:
    """
    Returns the effective settings for a user by layering their custom
    settings over their subscription tier's defaults.
    """
    tier = get_user_tier(cursor, user_id)
    settings = config.get_active_settings(tier).copy()  # Start with a copy of tier defaults
    user_data = cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not user_data:
        return settings

    for db_key, settings_key in CUSTOM_SETTINGS_MAPPING.items():
        if db_key in user_data.keys() and user_data[db_key] is not None:
            settings[settings_key] = user_data[db_key]

    return settings

@db_connection
def is_trade_open(cursor, user_id: int, coin_symbol: str):
    """Checks if a user already has an open trade for a specific symbol."""
    trade = cursor.execute(
        "SELECT id FROM trades WHERE user_id = ? AND coin_symbol = ? AND status = 'open'",
        (user_id, coin_symbol)
    ).fetchone()
    return trade is not None

@db_connection
def get_closed_trades(cursor, user_id: int):
    """Retrieves all closed trades for a specific user."""
    return cursor.execute(
        "SELECT coin_symbol, buy_price, sell_price FROM trades WHERE user_id = ? AND status = 'closed' AND sell_price IS NOT NULL",
        (user_id,)
    ).fetchall()

@db_connection
def get_global_top_trades(cursor, limit: int = 3):
    """Retrieves the top N most profitable closed trades across all users."""
    query = '''
        SELECT
            user_id,
            coin_symbol,
            buy_price,
            sell_price,
            ((sell_price - buy_price) / buy_price) * 100 AS pnl_percent
        FROM trades
        WHERE status = 'closed' AND sell_price IS NOT NULL AND ((sell_price - buy_price) / buy_price) > 0
        ORDER BY pnl_percent DESC
        LIMIT ?
    '''
    return cursor.execute(query, (limit,)).fetchall()

@db_connection
def is_on_watchlist(cursor, user_id: int, coin_symbol: str):
    """Checks if a user is already watching a specific symbol."""
    item = cursor.execute(
        "SELECT id FROM watchlist WHERE user_id = ? AND coin_symbol = ?",
        (user_id, coin_symbol)
    ).fetchone()
    return item is not None

@db_connection
def update_trade_stop_loss(cursor, trade_id: int, new_stop_loss: float):
    """Updates the stop-loss for a specific trade."""
    cursor.execute(
        "UPDATE trades SET stop_loss_price = ? WHERE id = ?",
        (new_stop_loss, trade_id)
    )

@db_connection
def update_dsl_stage(cursor, trade_id: int, new_stage: int):
    """Updates the DSL stage for a specific trade."""
    cursor.execute(
        "UPDATE trades SET current_dsl_stage = ? WHERE id = ?",
        (new_stage, trade_id)
    )

@db_connection
def log_trade(cursor, user_id: int, coin_symbol: str, buy_price: float, stop_loss: float, take_profit: float, mode: str = 'LIVE', trade_size_usdt: float | None = None, quantity: float | None = None, rsi_at_buy: float | None = None, peak_price: float | None = None):
    """Logs a new open trade for a user in the database."""
    if trade_size_usdt is not None and trade_size_usdt < 5:
        logger.warning(f"Trade for user {user_id} on {coin_symbol} below notional threshold: {trade_size_usdt}")
        return

    logger.info(f"Processing trade for user {user_id}, symbol {coin_symbol}...")
    # Ensure user exists before logging a trade
    get_or_create_user(cursor, user_id)
    cursor.execute(
        "INSERT INTO trades (user_id, coin_symbol, buy_price, status, stop_loss_price, take_profit_price, mode, trade_size_usdt, quantity, rsi_at_buy, peak_price) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, coin_symbol, buy_price, 'open', stop_loss, take_profit, mode, trade_size_usdt, quantity, rsi_at_buy, peak_price)
    )
    return cursor.lastrowid

@db_connection
def close_trade(cursor, trade_id: int, user_id: int, sell_price: float, close_reason: str, win_loss: str, pnl_percentage: float, closed_by: str) -> bool:
    import datetime
    now = datetime.datetime.utcnow().isoformat()
    cursor.execute(
        """
        UPDATE trades
        SET status = 'closed', sell_price = ?, close_reason = ?, win_loss = ?, pnl_percentage = ?, closed_at = ?, closed_by = ?
        WHERE id = ? AND user_id = ? AND status = 'open'
        """,
        (sell_price, close_reason, win_loss, pnl_percentage, now, closed_by, trade_id, user_id)
    )
    return cursor.rowcount > 0
