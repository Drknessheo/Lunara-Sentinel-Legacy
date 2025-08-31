import os
import sqlite3

import pytest

from modules import db_access

# Mock decrypt_data for API key tests
db_access.decrypt_data = lambda x: x.decode() if isinstance(x, bytes) else x

TEST_DB = "test_lunara_bot.db"


@pytest.fixture()
def setup_test_db():
    conn = sqlite3.connect(TEST_DB)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    db_access.initialize_database.__wrapped__(cursor)
    conn.commit()
    yield cursor
    conn.close()
    os.remove(TEST_DB)


def test_get_or_create_user(setup_test_db):
    cursor = setup_test_db
    user_id = 12345
    user = db_access.get_or_create_user(cursor, user_id)
    assert user["user_id"] == user_id
    # Should not create duplicate
    user2 = db_access.get_or_create_user(cursor, user_id)
    assert user2["user_id"] == user_id


def test_get_user_tier_admin(setup_test_db, monkeypatch):
    cursor = setup_test_db
    monkeypatch.setattr(db_access.config, "ADMIN_USER_ID", 999)
    assert db_access.get_user_tier(cursor, 999) == "PREMIUM"


def test_get_user_effective_settings(setup_test_db, monkeypatch):
    cursor = setup_test_db
    user_id = 222
    db_access.get_or_create_user(cursor, user_id)
    monkeypatch.setattr(
        db_access.config,
        "get_active_settings",
        lambda tier: {"RSI_BUY_THRESHOLD": 30, "RSI_SELL_THRESHOLD": 70},
    )
    settings = db_access.get_user_effective_settings.__wrapped__(cursor, user_id)
    assert "RSI_BUY_THRESHOLD" in settings


def test_api_key_encryption_decryption(setup_test_db):
    cursor = setup_test_db
    user_id = 333
    api_key = b"somekey"
    secret_key = b"somesecret"
    cursor.execute(
        "INSERT INTO users (user_id, api_key, secret_key) VALUES (?, ?, ?)",
        (user_id, api_key, secret_key),
    )
    result_api, result_secret = db_access.get_user_api_keys.__wrapped__(cursor, user_id)
    assert result_api == "somekey"
    assert result_secret == "somesecret"


def test_api_key_encryption_decryption_none(setup_test_db):
    cursor = setup_test_db
    user_id = 334
    db_access.get_or_create_user(cursor, user_id)
    result_api, result_secret = db_access.get_user_api_keys.__wrapped__(cursor, user_id)
    assert result_api is None
    assert result_secret is None


def test_watchlist_functions(setup_test_db):
    cursor = setup_test_db
    user_id = 444
    db_access.get_or_create_user(cursor, user_id)
    # Add to watchlist
    cursor.execute(
        "INSERT INTO watchlist (user_id, coin_symbol) VALUES (?, ?)",
        (user_id, "BTCUSDT"),
    )
    items = db_access.get_watched_items_by_user.__wrapped__(cursor, user_id)
    assert any(item["coin_symbol"] == "BTCUSDT" for item in items)


def test_trade_functions(setup_test_db):
    cursor = setup_test_db
    user_id = 445
    db_access.get_or_create_user(cursor, user_id)
    # Add a trade
    cursor.execute(
        "INSERT INTO trades (user_id, coin_symbol, buy_price, status) VALUES (?, ?, ?, ?)",
        (user_id, "BTCUSDT", 50000, "open"),
    )
    open_trades = db_access.get_open_trades.__wrapped__(cursor, user_id)
    assert any(trade["coin_symbol"] == "BTCUSDT" for trade in open_trades)
