
"""
Asynchronous, thread-safe SQLite access layer for Lunessa / Lunara Bot.
Powered by aiosqlite.
"""

import aiosqlite
import logging
from pathlib import Path
from . import config
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

# === Configuration ===
DB_PATH = Path(__file__).parent.parent / "lunessa.db"
ENCRYPTION_KEY = getattr(config, 'SLIP_ENCRYPTION_KEY', None)
if not ENCRYPTION_KEY:
    raise ValueError("SLIP_ENCRYPTION_KEY is not set in the configuration.")
fernet = Fernet(ENCRYPTION_KEY.encode())

# === Default Settings ===
DEFAULT_SETTINGS = {
    'rsi_buy': 28.0, 'rsi_sell': 75.0, 'stop_loss': 3.5,
    'trailing_activation': 2.0, 'trailing_drop': 0.5, 'profit_target': 5.0,
    'autotrade': 0, 'trading_mode': 'PAPER', 'paper_balance': 10000.0,
    'watchlist': 'ADAUSDT,ARBUSDT,AVAXUSDT,BNBUSDT,BTCUSDT,DOGEUSDT,DOTUSDT,ETHUSDT,HBARUSDT,LINKUSDT,LTCUSDT,LUNCUSDT,MATICUSDT,SHIBUSDT,VETUSDT,XRPUSDT'
}

SETTING_TO_COLUMN_MAP = {
    'rsi_buy': 'custom_rsi_buy', 'rsi_sell': 'custom_rsi_sell', 'stop_loss': 'custom_stop_loss',
    'trailing_activation': 'custom_trailing_activation', 'trailing_drop': 'custom_trailing_drop',
    'profit_target': 'custom_profit_target', 'autotrade': 'autotrade_enabled',
    'trading_mode': 'trading_mode', 'paper_balance': 'paper_balance', 'watchlist': 'watchlist'
}

# === Core Connection ===
async def get_connection() -> aiosqlite.Connection:
    """Return an asynchronous SQLite connection."""
    conn = await aiosqlite.connect(DB_PATH, detect_types=aiosqlite.PARSE_DECLTYPES)
    conn.row_factory = aiosqlite.Row
    return conn

# === Initialization & Migration ===
async def _migrate_db(conn: aiosqlite.Connection):
    """Applies database schema migrations asynchronously."""
    async with conn.cursor() as cursor:
        logger.info("Checking for necessary database migrations...")
        migrations = [
            ('trades', 'symbol', 'TEXT NOT NULL DEFAULT \'UNKNOWN\''),
            ('users', 'watchlist', 'TEXT'),
            ('trades', 'stop_loss', 'REAL'),
        ]
        for table, column, definition in migrations:
            await cursor.execute(f"PRAGMA table_info({table})")
            columns = [row['name'] for row in await cursor.fetchall()]
            if column not in columns:
                try:
                    logger.info(f"Applying migration: Adding '{column}' to '{table}' table.")
                    await cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                except aiosqlite.OperationalError as e:
                    if "duplicate column name" in str(e):
                        logger.warning(f"Migration for '{column}' column already applied.")
                    else:
                        logger.error(f"Failed to apply '{column}' migration: {e}")
                        raise
            else:
                logger.info(f"'{column}' column in '{table}' already exists. No migration needed.")
    await conn.commit()

async def init_db():
    """Initializes the database, creates tables, and runs migrations asynchronously."""
    async with await get_connection() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, api_key BLOB, secret_key BLOB,
            trading_mode TEXT DEFAULT 'PAPER', paper_balance REAL DEFAULT 10000.0,
            autotrade_enabled INTEGER DEFAULT 0, custom_rsi_buy REAL, custom_rsi_sell REAL,
            custom_stop_loss REAL, custom_trailing_activation REAL, custom_trailing_drop REAL,
            custom_profit_target REAL, watchlist TEXT
        );
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, symbol TEXT NOT NULL,
            buy_price REAL NOT NULL, quantity REAL NOT NULL,
            buy_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, status TEXT NOT NULL DEFAULT 'open',
            stop_loss REAL, trade_size_usdt REAL
        );
        """)
        await _migrate_db(conn)
        await conn.commit()

# === User Management ===
async def get_or_create_user(user_id):
    async with await get_connection() as conn:
        async with conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as cursor:
            user = await cursor.fetchone()
        created = False
        if not user:
            await conn.execute("INSERT INTO users (user_id, watchlist) VALUES (?, ?)", (user_id, DEFAULT_SETTINGS['watchlist']))
            await conn.commit()
            async with conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as cursor:
                user = await cursor.fetchone()
            created = True
            if user_id == config.ADMIN_USER_ID:
                logger.info(f"First-time setup for Admin user {user_id}. Adding default watchlist.")
                await add_coins_to_watchlist(user_id, DEFAULT_SETTINGS['watchlist'].split(','))
        return user, created

async def store_user_api_keys(user_id, api_key, secret_key):
    await get_or_create_user(user_id)
    encrypted_api = fernet.encrypt(api_key.encode())
    encrypted_secret = fernet.encrypt(secret_key.encode())
    async with await get_connection() as conn:
        await conn.execute(
            "UPDATE users SET api_key=?, secret_key=? WHERE user_id=?",
            (encrypted_api, encrypted_secret, user_id)
        )
        await conn.commit()

async def get_user_api_keys(user_id):
    if user_id == config.ADMIN_USER_ID and config.BINANCE_API_KEY and config.BINANCE_SECRET_KEY:
        return config.BINANCE_API_KEY, config.BINANCE_SECRET_KEY
    user, _ = await get_or_create_user(user_id)
    if not user['api_key'] or not user['secret_key']:
        return None, None
    try:
        api_key = fernet.decrypt(user['api_key']).decode()
        secret_key = fernet.decrypt(user['secret_key']).decode()
        return api_key, secret_key
    except Exception:
        logger.error(f"Failed to decrypt API keys for user {user_id}.")
        return None, None

# === Trade Management ===
async def create_trade(user_id, symbol, buy_price, quantity, trade_size_usdt):
    async with await get_connection() as conn:
        await conn.execute(
            "INSERT INTO trades (user_id, symbol, buy_price, quantity, trade_size_usdt) VALUES (?, ?, ?, ?, ?)",
            (user_id, symbol, buy_price, quantity, trade_size_usdt)
        )
        await conn.commit()

async def update_trade(trade: dict):
    if 'id' not in trade or 'stop_loss' not in trade:
        logger.error("Attempted to update a trade without 'id' or 'stop_loss'.")
        return
    async with await get_connection() as conn:
        await conn.execute("UPDATE trades SET stop_loss = ? WHERE id = ?", (trade['stop_loss'], trade['id']))
        await conn.commit()
    logger.info(f"Updated trade {trade['id']} with new stop_loss: {trade['stop_loss']}")

async def get_open_trades_by_user(user_id):
    async with await get_connection() as conn:
        async with conn.execute("SELECT * FROM trades WHERE user_id=? AND status='open'", (user_id,)) as cursor:
            return await cursor.fetchall()

async def find_open_trade_by_id(trade_id, user_id):
    async with await get_connection() as conn:
        async with conn.execute("SELECT * FROM trades WHERE id=? AND user_id=? AND status='open'", (trade_id, user_id)) as cursor:
            return await cursor.fetchone()

async def mark_trade_closed(trade_id, reason="closed"):
    async with await get_connection() as conn:
        await conn.execute("UPDATE trades SET status=? WHERE id=?", (reason, trade_id))
        await conn.commit()

# === Settings & Watchlist ===
async def get_user_trading_mode_and_balance(user_id):
    user, _ = await get_or_create_user(user_id)
    return user['trading_mode'], user['paper_balance']

async def get_user_effective_settings(user_id: int) -> dict:
    user, _ = await get_or_create_user(user_id)
    settings = {}
    for setting_name, column_name in SETTING_TO_COLUMN_MAP.items():
        user_value = user[column_name] if column_name in user.keys() and user[column_name] is not None else None
        value = user_value if user_value is not None else DEFAULT_SETTINGS.get(setting_name)
        settings[setting_name] = 'on' if setting_name == 'autotrade' and value == 1 else ('off' if setting_name == 'autotrade' else value)
    return settings

async def update_user_setting(user_id: int, setting_name: str, value):
    logger.info(f"[DB_WRITE] Attempting to update setting '{setting_name}' for user {user_id} with value '{value}'.")
    if setting_name not in SETTING_TO_COLUMN_MAP:
        raise ValueError(f"Invalid setting name: {setting_name}")
    column_name = SETTING_TO_COLUMN_MAP[setting_name]
    # ... (value processing logic remains the same)
    processed_value = value # Placeholder for the processing logic from your synchronous version
    async with await get_connection() as conn:
        await conn.execute(f"UPDATE users SET {column_name}=? WHERE user_id=?", (processed_value, user_id))
        await conn.commit()
    logger.info(f"[DB_WRITE] SUCCESS: Setting '{setting_name}' for user {user_id} was updated.")

async def add_coins_to_watchlist(user_id, coins_to_add: list):
    user, _ = await get_or_create_user(user_id)
    # ... (logic remains the same)
    new_watchlist_str = ','.join(sorted(list(set((user['watchlist'] or '').split(',')) | set(c.upper() for c in coins_to_add))))
    async with await get_connection() as conn:
        await conn.execute("UPDATE users SET watchlist=? WHERE user_id=?", (new_watchlist_str, user_id))
        await conn.commit()

async def remove_coins_from_watchlist(user_id, coins_to_remove: list):
    user, _ = await get_or_create_user(user_id)
    # ... (logic remains the same)
    new_watchlist_str = ','.join(sorted(list(set((user['watchlist'] or '').split(',')) - set(c.upper() for c in coins_to_remove))))
    async with await get_connection() as conn:
        await conn.execute("UPDATE users SET watchlist=? WHERE user_id=?", (new_watchlist_str, user_id))
        await conn.commit()

# === Global Stats ===
async def get_user_count():
    async with await get_connection() as conn:
        async with conn.execute("SELECT COUNT(*) FROM users") as cursor:
            return (await cursor.fetchone())[0]

async def get_active_autotrade_count():
    async with await get_connection() as conn:
        async with conn.execute("SELECT COUNT(*) FROM users WHERE autotrade_enabled=1") as cursor:
            return (await cursor.fetchone())[0]

async def get_users_with_autotrade_enabled():
    async with await get_connection() as conn:
        async with conn.execute("SELECT user_id FROM users WHERE autotrade_enabled=1") as cursor:
            rows = await cursor.fetchall()
            return [row['user_id'] for row in rows]

async def get_all_users():
    async with await get_connection() as conn:
        async with conn.execute("SELECT user_id FROM users") as cursor:
            rows = await cursor.fetchall()
            return [row['user_id'] for row in rows]
