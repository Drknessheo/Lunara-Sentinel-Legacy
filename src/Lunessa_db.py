"""
Lunessa_db.py — Reserved for Lunessa DB helpers and migrations.

This file is a safe, inert scaffold intended to hold future structured
audit/memory logic. It is safe to import (no top-level side-effects).

The original, uncleaned content from the repository is preserved in
ORIGINAL_BAK below so historical knowledge is retained for later
restoration or refactor.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import List, Optional

logger = logging.getLogger(__name__)


# Configuration helpers (best-effort import from repo config)
try:
    import config  # type: ignore

    DB_NAME = getattr(config, "DB_NAME", "lunara_bot.db")
except Exception:
    config = None  # type: ignore
    DB_NAME = "lunara_bot.db"


try:
    from security import decrypt_data, encrypt_data  # type: ignore
except Exception:

    def encrypt_data(value: str) -> bytes:  # pragma: no cover - placeholder
        raise NotImplementedError(
            "security.encrypt_data not available in this environment"
        )

    def decrypt_data(value: bytes) -> str:  # pragma: no cover - placeholder
        raise NotImplementedError(
            "security.decrypt_data not available in this environment"
        )


def get_db_connection() -> sqlite3.Connection:
    """Return a sqlite3 connection configured with Row factory."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_database() -> None:
    """Create core tables used by the bot. Safe to call repeatedly."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                coin_symbol TEXT NOT NULL,
                buy_price REAL NOT NULL,
                buy_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL,
                sell_price REAL,
                stop_loss_price REAL,
                take_profit_price REAL,
                peak_price REAL,
                mode TEXT DEFAULT 'LIVE',
                trade_size_usdt REAL,
                quantity REAL,
                close_reason TEXT
            )
            """
        )

        cur.execute(
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
                trading_mode TEXT DEFAULT 'LIVE',
                paper_balance REAL DEFAULT 10000.0,
                autotrade_enabled INTEGER DEFAULT NULL
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                coin_symbol TEXT NOT NULL,
                add_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, coin_symbol)
            )
            """
        )

        conn.commit()
        logger.debug("Lunessa DB: core tables ensured")


def get_or_create_user(user_id: int) -> sqlite3.Row:
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row:
            return row
        conn.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        return conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()


def get_autotrade_status(user_id: int) -> bool:
    user = get_or_create_user(user_id)
    if user is not None and user["autotrade_enabled"] is not None:
        return bool(user["autotrade_enabled"])
    if config and getattr(config, "ADMIN_USER_ID", None) == user_id:
        return True
    return False


def set_autotrade_status(user_id: int, enabled: bool) -> None:
    get_or_create_user(user_id)
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE users SET autotrade_enabled = ? WHERE user_id = ?",
            (int(enabled), user_id),
        )


def log_trade(
    user_id: int,
    coin_symbol: str,
    buy_price: float,
    stop_loss: Optional[float],
    take_profit: Optional[float],
    mode: str = "LIVE",
    trade_size_usdt: Optional[float] = None,
    quantity: Optional[float] = None,
) -> None:
    get_or_create_user(user_id)
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO trades (user_id, coin_symbol, buy_price, status, stop_loss_price, take_profit_price, mode, trade_size_usdt, quantity) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                coin_symbol,
                buy_price,
                "open",
                stop_loss,
                take_profit,
                mode,
                trade_size_usdt,
                quantity,
            ),
        )


def get_open_trades(user_id: int) -> List[sqlite3.Row]:
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT id, coin_symbol, buy_price, buy_timestamp, stop_loss_price, take_profit_price FROM trades WHERE user_id = ? AND status = 'open'",
            (user_id,),
        ).fetchall()


def get_all_open_trades() -> List[sqlite3.Row]:
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT id, user_id, coin_symbol, buy_price, stop_loss_price, take_profit_price, peak_price, mode, trade_size_usdt, quantity, buy_timestamp FROM trades WHERE status = 'open'"
        ).fetchall()


def get_trade_by_id(trade_id: int, user_id: int) -> Optional[sqlite3.Row]:
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT id, coin_symbol FROM trades WHERE id = ? AND user_id = ? AND status = 'open'",
            (trade_id, user_id),
        ).fetchone()


def close_trade(
    trade_id: int, user_id: int, sell_price: float, close_reason: Optional[str] = None
) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE trades SET status = 'closed', sell_price = ?, close_reason = ? WHERE id = ? AND user_id = ? AND status = 'open'",
            (sell_price, close_reason, trade_id, user_id),
        )
        return cursor.rowcount > 0


def activate_trailing_stop(trade_id: int, peak_price: float) -> None:
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE trades SET peak_price = ? WHERE id = ?", (peak_price, trade_id)
        )


def get_closed_trades(user_id: int) -> List[sqlite3.Row]:
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT coin_symbol, buy_price, sell_price FROM trades WHERE user_id = ? AND status = 'closed' AND sell_price IS NOT NULL",
            (user_id,),
        ).fetchall()


def get_top_closed_trades(user_id: int, limit: int = 3) -> List[sqlite3.Row]:
    with get_db_connection() as conn:
        query = """
            SELECT coin_symbol, buy_price, sell_price, ((sell_price - buy_price) / buy_price) * 100 AS pnl_percent
            FROM trades
            WHERE user_id = ? AND status = 'closed' AND sell_price IS NOT NULL AND ((sell_price - buy_price) / buy_price) > 0
            ORDER BY pnl_percent DESC
            LIMIT ?
        """
        return conn.execute(query, (user_id, limit)).fetchall()


def get_global_top_trades(limit: int = 3) -> List[sqlite3.Row]:
    with get_db_connection() as conn:
        query = """
            SELECT user_id, coin_symbol, buy_price, sell_price, ((sell_price - buy_price) / buy_price) * 100 AS pnl_percent
            FROM trades
            WHERE status = 'closed' AND sell_price IS NOT NULL AND ((sell_price - buy_price) / buy_price) > 0
            ORDER BY pnl_percent DESC
            LIMIT ?
        """
        return conn.execute(query, (limit,)).fetchall()


def set_user_trading_mode(user_id: int, mode: str):
    """Sets the user's trading mode ('LIVE' or 'PAPER')."""
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE users SET trading_mode = ? WHERE user_id = ?",
            (mode.upper(), user_id),
        )


def get_user_trading_mode_and_balance(user_id: int):
    """Gets the user's trading mode and paper balance."""
    user = get_or_create_user(user_id)
    return user["trading_mode"], user["paper_balance"]


def update_paper_balance(user_id: int, amount_change: float):
    """Updates a user's paper balance by adding or subtracting an amount."""
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE users SET paper_balance = paper_balance + ? WHERE user_id = ?",
            (amount_change, user_id),
        )


def reset_paper_account(user_id: int):
    """Resets a user's paper balance to the default and closes all paper trades."""
    with get_db_connection() as conn:
        # Reset balance
        conn.execute(
            "UPDATE users SET paper_balance = ? WHERE user_id = ?",
            (config.PAPER_STARTING_BALANCE, user_id),
        )
        # Close all open paper trades for that user
        conn.execute(
            "UPDATE trades SET status = 'closed', sell_price = buy_price, close_reason = 'Reset' WHERE user_id = ? AND mode = 'PAPER' AND status = 'open'",
            (user_id,),
        )


def get_all_watchlist_items():
    """Retrieves all items from the watchlist for all users."""
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT id, user_id, coin_symbol, add_timestamp FROM watchlist"
        ).fetchall()


def remove_from_watchlist(item_id: int):
    """Removes an item from the watchlist by its ID."""
    with get_db_connection() as conn:
        conn.execute("DELETE FROM watchlist WHERE id = ?", (item_id,))


def get_watched_items_by_user(user_id: int):
    """Retrieves all watched symbols for a specific user."""
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT coin_symbol, add_timestamp FROM watchlist WHERE user_id = ?",
            (user_id,),
        ).fetchall()


# --- User API Key and Subscription Functions ---


def store_user_api_keys(user_id: int, api_key: str, secret_key: str):
    """Encrypts and stores a user's Binance API keys."""
    get_or_create_user(user_id)  # Ensure user exists
    encrypted_api_key = encrypt_data(api_key)
    encrypted_secret_key = encrypt_data(secret_key)
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE users SET api_key = ?, secret_key = ? WHERE user_id = ?",
            (encrypted_api_key, encrypted_secret_key, user_id),
        )


def get_user_api_keys(user_id: int) -> tuple[str | None, str | None]:
    """
    Retrieves and decrypts a user's Binance API keys.
    For the admin user, it returns the keys directly from the .env configuration.
    """
    # As the father of the bot, you get your keys directly from the sacred .env scroll.
    if user_id == config.ADMIN_USER_ID:
        return config.BINANCE_API_KEY, config.BINANCE_SECRET_KEY

    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT api_key, secret_key FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()

    if not row or not row["api_key"] or not row["secret_key"]:
        return None, None

    api_key = decrypt_data(row["api_key"])
    secret_key = decrypt_data(row["secret_key"])
    return api_key, secret_key


def get_user_tier(user_id: int) -> str:
    """Retrieves a user's subscription tier."""
    # As the father of the bot, you are always granted Premium status.
    if user_id == config.ADMIN_USER_ID:
        return "PREMIUM"

    user = get_or_create_user(user_id)
    # Future logic: check if subscription_expires is in the past and downgrade if so.
    return user["subscription_tier"]


def update_user_tier(user_id: int, tier: str, expiration_date=None):
    """Updates a user's subscription tier and optional expiration date."""
    get_or_create_user(user_id)  # Ensure user exists
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE users SET subscription_tier = ?, subscription_expires = ? WHERE user_id = ?",
            (tier.upper(), expiration_date, user_id),
        )


def get_all_user_ids() -> list[int]:
    """Retrieves a list of all user IDs from the database."""
    with get_db_connection() as conn:
        return [
            row["user_id"]
            for row in conn.execute("SELECT user_id FROM users").fetchall()
        ]
