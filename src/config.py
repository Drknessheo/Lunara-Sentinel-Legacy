import os
import sys
import itertools
from dotenv import load_dotenv

# 1. --- Environment Loading ---
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")

if os.path.exists(dotenv_path):
    print(f"[CONFIG] Loading environment from: {dotenv_path}")
    load_dotenv(dotenv_path=dotenv_path)
else:
    print(f"[CONFIG] Warning: .env file not found at {dotenv_path}. Relying on system environment variables.")

# 2. --- Core Credentials & Keys ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")
REDIS_URL = os.getenv("REDIS_URL")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))

# --- Gemini Ministry: Diplomatic Pouch (Key Rotation) ---
GEMINI_KEY_1 = os.getenv("GEMINI_KEY_1")
GEMINI_KEY_2 = os.getenv("GEMINI_KEY_2")

# Create a cycle of all available, valid keys.
_GEMINI_API_KEYS = [key for key in [GEMINI_KEY_1, GEMINI_KEY_2] if key]
_gemini_key_cycler = itertools.cycle(_GEMINI_API_KEYS) if _GEMINI_API_KEYS else None

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

def get_next_gemini_key():
    """Rotates through the available Gemini API keys, providing the next one in the cycle."""
    if not _gemini_key_cycler:
        return None
    next_key = next(_gemini_key_cycler)
    # logger.debug(f"Using Gemini Key ending in ...{next_key[-4:]}") # Uncomment for debugging
    return next_key

# Unify encryption keys
ENCRYPTION_KEY_STR = (
    os.getenv("SLIP_ENCRYPTION_KEY")
    or os.getenv("BINANCE_ENCRYPTION_KEY")
    or os.getenv("SANDPAPER_ENCRYPTION_KEY")
)

if not ENCRYPTION_KEY_STR:
    raise ValueError(
        "CRITICAL: No encryption key found. Set SLIP_ENCRYPTION_KEY in your .env file or environment."
    )

SLIP_ENCRYPTION_KEY = ENCRYPTION_KEY_STR
BINANCE_ENCRYPTION_KEY = ENCRYPTION_KEY_STR.encode()
SANDPAPER_ENCRYPTION_KEY = ENCRYPTION_KEY_STR.encode()

# 3. --- Sanity Checks & Debugging ---
if not TELEGRAM_BOT_TOKEN:
    print("Warning: TELEGRAM_BOT_TOKEN is not set.")
if not REDIS_URL:
    print("Warning: REDIS_URL is not set.")
if not ADMIN_USER_ID:
    print("Warning: ADMIN_USER_ID is not set.")
if not _GEMINI_API_KEYS:
    print("Warning: No Gemini API keys (GEMINI_KEY_1, GEMINI_KEY_2) are set. Autotrade intelligence will be disabled.")

def safe_print_config():
    """
    Prints a sanitized version of the environment variables for debugging.
    """
    print("[CONFIG] Sanitized Environment Variables:")
    safe_keys = [
        "MODE", "ENV", "VERSION", "CHAT_ID", "ADMIN_USER_ID", "GDRIVE_REMOTE_NAME",
        "DB_NAME", "AI_TRADE_INTERVAL_MINUTES", "TELEGRAM_SYNC_LOG_ENABLED",
        "BTC_ALERT_THRESHOLD_PERCENT", "HELD_TOO_LONG_HOURS",
        "NEAR_STOP_LOSS_THRESHOLD_PERCENT", "NEAR_TAKE_PROFIT_THRESHOLD_PERCENT",
        "RSI_BUY_RECOVERY_THRESHOLD", "WATCHLIST_TIMEOUT_HOURS",
        "PAPER_TRADE_SIZE_USDT", "PAPER_STARTING_BALANCE", "GEMINI_MODEL", "USE_FAKE_REDIS"
    ]
    for key, value in os.environ.items():
        is_safe = key.upper() in safe_keys or not any(
            s in key.upper() for s in ["API", "KEY", "TOKEN", "SECRET"]
        )
        if is_safe:
            print(f"  - {key}: {value}")
        else:
            print(f"  - {key}: **** MASKED ****")

# 4. --- Application & Bot Behavior Settings ---
USE_FAKE_REDIS = os.getenv("USE_FAKE_REDIS", "False").lower() in ["true", "1", "t"]
PER_TRADE_ALLOCATION_PERCENT = 5.0
TELEGRAM_SYNC_LOG_ENABLED = True
AI_TRADE_INTERVAL_MINUTES = 10
TRADE_MONITOR_INTERVAL_SECONDS = 20
DB_NAME = "lunessa.db"
WEBHOOK_HMAC_SECRET = os.getenv("WEBHOOK_HMAC_SECRET")

# --- Global Market & Bot Behavior Settings ---
BTC_ALERT_THRESHOLD_PERCENT = 2.0
HELD_TOO_LONG_HOURS = 48
NEAR_STOP_LOSS_THRESHOLD_PERCENT = 2.0
NEAR_TAKE_PROFIT_THRESHOLD_PERCENT = 2.0
RSI_BUY_RECOVERY_THRESHOLD = 32.0
WATCHLIST_TIMEOUT_HOURS = 24

# --- Paper Trading ---
PAPER_TRADE_SIZE_USDT = 1000.0
PAPER_STARTING_BALANCE = 10000.0

# 5. --- Subscription Tier Configuration ---
SUBSCRIPTION_TIERS = {}
DEFAULT_SETTINGS = {"PROFIT_TARGET_PERCENTAGE": 1.0}
def get_active_settings(tier: str):
    return SUBSCRIPTION_TIERS.get(tier.upper(), SUBSCRIPTION_TIERS.get("FREE", {}))

# 6. --- Module Unification ---
try:
    sys.modules.setdefault("config", sys.modules[__name__])
except Exception:
    pass
