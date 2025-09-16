# blueprint/lunessasignels/lunara-bot/src/db.py
"""
Thread-safe SQLite access layer for Lunessa / Lunara Bot.
Each thread gets its own connection; no more check_same_thread hacks.
"""

import sqlite3
import threading
from pathlib import Path

# === Configuration ===
DB_PATH = Path(__file__).parent / "lunessa.db"

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
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                telegram_id TEXT UNIQUE,
                api_key TEXT,
                api_secret TEXT,
                settings TEXT
            )
        """
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                symbol TEXT,
                status TEXT,
                buy_price REAL,
                sell_price REAL,
                quantity REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

def get_or_create_user(telegram_id):
    """
    Retrieves a user by their Telegram ID. If the user does not exist,
    a new record is created.
    Returns a tuple of (user_data, created_boolean).
    """
    conn = get_connection()
    # Ensure telegram_id is a string for consistent lookups
    str_telegram_id = str(telegram_id)
    
    user = conn.execute("SELECT * FROM users WHERE telegram_id=?", (str_telegram_id,)).fetchone()
    if user:
        return user, False  # User existed

    # User does not exist, create them with default empty settings
    try:
        with conn:
            cursor = conn.execute(
                "INSERT INTO users (telegram_id, settings) VALUES (?, ?)",
                (str_telegram_id, '{}')
            )
            # If we inserted a row, lastrowid will be the new user's primary key
            if cursor.rowcount > 0:
                new_user = conn.execute("SELECT * FROM users WHERE telegram_id=?", (str_telegram_id,)).fetchone()
                return new_user, True # User was created
    except sqlite3.IntegrityError:
        # This handles a rare race condition: if another thread created the user
        # between our SELECT and INSERT, the INSERT will fail due to the UNIQUE constraint.
        # In this case, we simply fetch the now-existing user.
        pass

    # If the INSERT was ignored or failed due to a race condition, fetch the user that must now exist
    user = conn.execute("SELECT * FROM users WHERE telegram_id=?", (str_telegram_id,)).fetchone()
    return user, False

def fetch_user(telegram_id):
    conn = get_connection()
    return conn.execute("SELECT * FROM users WHERE telegram_id=?", (str(telegram_id),)).fetchone()

def insert_user(telegram_id, api_key, api_secret, settings):
    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO users (telegram_id, api_key, api_secret, settings) VALUES (?,?,?,?)",
            (str(telegram_id), api_key, api_secret, settings)
        )

def fetch_open_trades(user_id):
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
