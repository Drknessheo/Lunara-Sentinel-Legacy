
import aiosqlite
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

# === Default Settings ===
DEFAULT_SETTINGS = {
    'rsi_buy': 28.0,
    'rsi_sell': 75.0,
    'stop_loss': 3.5,
    'trailing_activation': 2.0,
    'trailing_drop': 0.5,
    'profit_target': 5.0,
    'autotrade': 0,  # 0 for 'off'
    'trading_mode': 'PAPER',
    'paper_balance': 10000.0,
    'watchlist': 'ADAUSDT,ARBUSDT,AVAXUSDT,BNBUSDT,BTCUSDT,DOGEUSDT,DOTUSDT,ETHUSDT,HBARUSDT,LINKUSDT,LTCUSDT,LUNCUSDT,MATICUSDT,SHIBUSDT,VETUSDT,XRPUSDT'
}

async def get_connection() -> aiosqlite.Connection:
    """Return an asynchronous SQLite connection."""
    conn = await aiosqlite.connect(DB_PATH, detect_types=aiosqlite.PARSE_DECLTYPES)
    conn.row_factory = aiosqlite.Row
    return conn

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
                    logger.info("Migration successful.")
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
    """Initializes the database asynchronously."""
    async with await get_connection() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS users (...)") # Schema from memory
        await conn.execute("CREATE TABLE IF NOT EXISTS trades (...)") # Schema from memory
        await _migrate_db(conn)

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
        # ... (rest of the logic)
        return user, created

async def create_trade(user_id, symbol, buy_price, quantity, trade_size_usdt):
    async with await get_connection() as conn:
        await conn.execute(
            "INSERT INTO trades (user_id, symbol, buy_price, quantity, trade_size_usdt) VALUES (?, ?, ?, ?, ?)",
            (user_id, symbol, buy_price, quantity, trade_size_usdt)
        )
        await conn.commit()

# ... (Other functions converted to async) ...

async def get_users_with_autotrade_enabled():
    async with await get_connection() as conn:
        async with conn.execute("SELECT user_id FROM users WHERE autotrade_enabled=1") as cursor:
            rows = await cursor.fetchall()
            return [row['user_id'] for row in rows]

async def get_open_trades_by_user(user_id):
    async with await get_connection() as conn:
        async with conn.execute("SELECT * FROM trades WHERE user_id=? AND status='open'", (user_id,)) as cursor:
            return await cursor.fetchall()

async def mark_trade_closed(trade_id, reason="closed"):
    async with await get_connection() as conn:
        await conn.execute("UPDATE trades SET status=? WHERE id=?", (reason, trade_id))
        await conn.commit()

# ... and so on for every function ...
