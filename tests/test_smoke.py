import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.ext import ContextTypes

# Assume pytest is run from the root of the repo, so src is importable.
from src import autotrade_settings, config, main, slip_manager
from src.modules import db_access as db

# --- Fixtures ---


@pytest.fixture(scope="function", autouse=True)
def test_db(monkeypatch):
    """
    Fixture to create and use a temporary database file for each test function.
    """
    fd, db_path = tempfile.mkstemp()
    os.close(fd)
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.initialize_database()
    yield
    os.unlink(db_path)


@pytest.fixture
def mock_redis(monkeypatch):
    """
    Mocks the redis.from_url call to return a fake Redis client (fakeredis).
    This allows testing Redis interactions without a running Redis server.
    """
    # Using a real in-memory redis implementation is more robust than a MagicMock
    try:
        import fakeredis

        fake_client = fakeredis.FakeRedis(decode_responses=True)
        monkeypatch.setattr("redis.from_url", lambda *args, **kwargs: fake_client)
        return fake_client
    except ImportError:
        pytest.skip("fakeredis not installed, skipping redis-dependent tests")


@pytest.fixture(autouse=True)
def mock_config(monkeypatch):
    """Mocks essential config values for tests."""
    monkeypatch.setattr(config, "ADMIN_USER_ID", 12345)

    def mock_get_active_settings(tier: str):
        if tier == "FREE":
            return {"PROFIT_TARGET_PERCENTAGE": 1.5}
        if tier == "GOLD":
            return {"PROFIT_TARGET_PERCENTAGE": 2.5}
        return {"PROFIT_TARGET_PERCENTAGE": 1.0}

    monkeypatch.setattr(config, "get_active_settings", mock_get_active_settings)
    monkeypatch.setattr(config, "DEFAULT_SETTINGS", {"PROFIT_TARGET_PERCENTAGE": 1.0})


# --- Test Cases ---


def test_get_user_effective_settings(test_db):
    """
    Unit test for the settings merging logic in db_access.py.
    Ensures that settings are correctly layered: Default -> Tier -> User Custom DB.
    """
    user_id = 999
    db.get_or_create_user_db(user_id)  # Creates user with FREE tier

    # 1. Check FREE tier settings
    settings = db.get_user_effective_settings(user_id)
    assert settings["PROFIT_TARGET_PERCENTAGE"] == 1.5

    # 2. Upgrade to GOLD tier and check again
    db.update_user_subscription(user_id, "GOLD", "2099-01-01")
    settings = db.get_user_effective_settings(user_id)
    assert settings["PROFIT_TARGET_PERCENTAGE"] == 2.5

    # 3. Set a custom override in the database
    db.update_user_setting(user_id, "profit_target", 10.0)
    settings = db.get_user_effective_settings(user_id)
    assert settings["PROFIT_TARGET_PERCENTAGE"] == 10.0


def test_autotrade_settings_flow(mock_redis):
    """
    Unit test for the autotrade_settings module.
    Verifies it correctly uses Redis to store and retrieve user setting overrides.
    """
    user_id = 123
    settings = {"TRADE_SIZE_USDT": 50, "RSI_BUY_THRESHOLD": 35}
    redis_key = f"autotrade:settings:{user_id}"

    # Test setting the value
    autotrade_settings.set_user_settings(user_id, settings)
    stored_val = mock_redis.get(redis_key)
    assert stored_val is not None
    assert json.loads(stored_val) == settings

    # Test getting the value
    retrieved_settings = autotrade_settings.get_user_settings(user_id)
    assert retrieved_settings == settings


def test_slip_manager_encryption(mock_redis, monkeypatch):
    """
    Unit test for slip_manager.py encryption contract.
    - It should encrypt/decrypt correctly with a key.
    - It should fail gracefully (no creation, None on read) without a key.
    """
    trade_id = "test_trade_123"
    slip_data = {"symbol": "BTCUSDT", "price": 50000}

    # --- Case 1: Encryption key IS present ---
    monkeypatch.setenv(
        "SLIP_ENCRYPTION_KEY", slip_manager.Fernet.generate_key().decode()
    )
    slip_manager.get_fernet.cache_clear()  # Clear lru_cache

    # Create and store
    slip_manager.create_and_store_slip(trade_id, slip_data)
    assert mock_redis.exists(f"trade:{trade_id}:data")

    # Get and decrypt
    decrypted_data = slip_manager.get_and_decrypt_slip(trade_id)
    assert decrypted_data == slip_data

    # --- Case 2: Encryption key is NOT present ---
    monkeypatch.delenv("SLIP_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("BINANCE_ENCRYPTION_KEY", raising=False)
    slip_manager.get_fernet.cache_clear()

    # Attempt to create should fail
    with pytest.raises(ValueError, match="Encryption key is not configured"):
        slip_manager.create_and_store_slip("another_trade", slip_data)

    # Attempt to get should return None without crashing
    assert slip_manager.get_and_decrypt_slip(trade_id) is None


@pytest.mark.asyncio
async def test_settings_and_myprofile_flow(test_db, mock_redis):
    """
    Integration test for the /settings -> /myprofile command flow.
    This verifies the full settings-merge logic as seen by the user.
    """
    admin_id = config.ADMIN_USER_ID
    db.get_or_create_user_db(admin_id)
    db.update_user_subscription(admin_id, "GOLD", "2099-01-01")

    # --- Mock Telegram objects ---
    update = MagicMock(spec=Update)
    update.effective_user.id = admin_id
    update.effective_user.username = "test_admin"
    # reply_text needs to be an Awaitable Mock
    update.message.reply_text = AsyncMock()

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    # --- 1. Admin uses /settings to set an override ---
    context.args = ["PROFIT_TARGET_PERCENTAGE=9.9", "TRADE_SIZE_USDT=123"]
    await main.settings_command(update, context)

    # Verify it was stored in Redis via autotrade_settings
    stored_settings = autotrade_settings.get_user_settings(admin_id)
    assert stored_settings.get("PROFIT_TARGET_PERCENTAGE") == 9.9
    assert stored_settings.get("TRADE_SIZE_USDT") == 123

    # --- 2. Set a legacy Redis key to test fallback merging ---
    legacy_key = f"user:{admin_id}:settings"
    mock_redis.set(legacy_key, json.dumps({"RSI_BUY_THRESHOLD": 25}))

    # --- 3. Admin uses /myprofile to view merged settings ---
    context.args = []
    await main.myprofile_command(update, context)

    # --- 4. Assert the output of /myprofile reflects the merged values ---
    update.message.reply_text.assert_called_once()
    # Get the text passed to the last call of the mock
    reply_text = update.message.reply_text.call_args[0][0]

    # Check that the /settings override is present
    assert "Stop Loss: 9.9%" in reply_text

    # Check that the legacy key value was merged
    assert "RSI Buy: 25" in reply_text

    # Check that a tier-based setting (not overridden) is still there
    # Our mock config doesn't have RSI_SELL_THRESHOLD, so we check a known one.
    # The DB default for `custom_trailing_activation` is 1.5.
    assert "Trailing Activation: 1.5%" in reply_text
