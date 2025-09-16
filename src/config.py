import os
import sys
from dotenv import load_dotenv

# 1. --- Environment Loading ---
# This is the most critical step. Load the .env file before any other configuration
# is accessed. The path is calculated by going two levels up from this file's
# directory (src -> root).
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")

if os.path.exists(dotenv_path):
    print(f"[CONFIG] Loading environment from: {dotenv_path}")
    load_dotenv(dotenv_path=dotenv_path)
else:
    print(f"[CONFIG] Warning: .env file not found at {dotenv_path}. Relying on system environment variables.")

# 2. --- Core Credentials & Keys ---
# Access keys immediately after loading the environment.
# Fail-fast if essential keys are missing.

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")
REDIS_URL = os.getenv("REDIS_URL")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))

# Unify encryption keys. The bot primarily uses SLIP_ENCRYPTION_KEY.
# We fall back to other names for compatibility but use one canonical variable.
ENCRYPTION_KEY_STR = (
    os.getenv("SLIP_ENCRYPTION_KEY")
    or os.getenv("BINANCE_ENCRYPTION_KEY")
    or os.getenv("SANDPAPER_ENCRYPTION_KEY")
)

if not ENCRYPTION_KEY_STR:
    raise ValueError(
        "CRITICAL: No encryption key found. Set SLIP_ENCRYPTION_KEY in your .env file or environment."
    )

# The application expects these specific variables to be available.
SLIP_ENCRYPTION_KEY = ENCRYPTION_KEY_STR
# Some legacy parts of the code might expect bytes, so we provide them.
BINANCE_ENCRYPTION_KEY = ENCRYPTION_KEY_STR.encode()
SANDPAPER_ENCRYPTION_KEY = ENCRYPTION_KEY_STR.encode()

# 3. --- Sanity Checks & Debugging ---
if not TELEGRAM_BOT_TOKEN:
    print("Warning: TELEGRAM_BOT_TOKEN is not set.")
if not REDIS_URL:
    print("Warning: REDIS_URL is not set.")
if not ADMIN_USER_ID:
    print("Warning: ADMIN_USER_ID is not set.")

def safe_print_config():
    """
    Prints a sanitized version of the environment variables for debugging,
    masking sensitive keys to prevent them from being exposed in logs.
    """
    print("[CONFIG] Sanitized Environment Variables:")
    safe_keys = [
        "MODE", "ENV", "VERSION", "CHAT_ID", "ADMIN_USER_ID", "GDRIVE_REMOTE_NAME",
        "DB_NAME", "AI_TRADE_INTERVAL_MINUTES", "TELEGRAM_SYNC_LOG_ENABLED",
        "BTC_ALERT_THRESHOLD_PERCENT", "HELD_TOO_LONG_HOURS",
        "NEAR_STOP_LOSS_THRESHOLD_PERCENT", "NEAR_TAKE_PROFIT_THRESHOLD_PERCENT",
        "RSI_BUY_RECOVERY_THRESHOLD", "WATCHLIST_TIMEOUT_HOURS",
        "PAPER_TRADE_SIZE_USDT", "PAPER_STARTING_BALANCE",
    ]
    for key, value in os.environ.items():
        is_safe = key.upper() in safe_keys or not any(
            s in key.upper() for s in ["API", "KEY", "TOKEN", "SECRET"]
        )
        if is_safe:
            print(f"  - {key}: {value}")
        else:
            print(f"  - {key}: **** MASKED ****")

# Uncomment the line below for verbose startup debugging
# safe_print_config()

# 4. --- Application & Bot Behavior Settings ---

AI_MONITOR_COINS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT", "XRPUSDT", "DOGEUSDT",
    "DOTUSDT", "AVAXUSDT", "LINKUSDT", "ARBUSDT", "OPUSDT", "LTCUSDT", "TRXUSDT",
    "SHIBUSDT", "PEPEUSDT", "UNIUSDT", "SUIUSDT", "INJUSDT", "CTKUSDT", "ENAUSDT",
]

PER_TRADE_ALLOCATION_PERCENT = 5.0
TELEGRAM_SYNC_LOG_ENABLED = True
AI_TRADE_INTERVAL_MINUTES = 10

DB_NAME = "lunessa.db"
WEBHOOK_HMAC_SECRET = os.getenv("WEBHOOK_HMAC_SECRET")
if not WEBHOOK_HMAC_SECRET:
    print("Warning: WEBHOOK_HMAC_SECRET is not set. Webhook verification will be skipped.")

GEMINI_API_URL = os.getenv("GEMINI_API_URL", "https://api.gemini.example/analysis")

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
SUBSCRIPTION_TIERS = {
    "FREE": {
        "NAME": "Free",
        "RSI_BUY_THRESHOLD": 30.0,
        "RSI_SELL_THRESHOLD": 70.0,
        "PROFIT_TARGET_PERCENTAGE": 1.0,
        "STOP_LOSS_PERCENTAGE": 5.0,
        "USE_TRAILING_TAKE_PROFIT": False,
        "USE_BOLLINGER_BANDS": False,
        "ALLOWED_COIN_TYPES": ["MAJOR"],
    },
    "PREMIUM": {
        "NAME": "Premium",
        "RSI_BUY_THRESHOLD": 30.0,
        "RSI_SELL_THRESHOLD": 70.0,
        "PROFIT_TARGET_PERCENTAGE": 1.0,
        "STOP_LOSS_PERCENTAGE": 4.0,
        "USE_TRAILING_TAKE_PROFIT": True,
        "TRAILING_PROFIT_ACTIVATION_PERCENT": 7.0,
        "TRAILING_STOP_DROP_PERCENT": 3.0,
        "USE_BOLLINGER_BANDS": True,
        "BOLL_PERIOD": 20,
        "BOLL_STD_DEV": 2,
        "BOLL_SQUEEZE_ALERT_ENABLED": True,
        "BOLL_SQUEEZE_THRESHOLD": 0.08,
        "RSI_OVERBOUGHT_ALERT_THRESHOLD": 80.0,
        "RSI_BEARISH_EXIT_THRESHOLD": 65.0,
        "DSLA_MODE": "step_ladder",
        "DSLA_LADDER": [
            {"profit": 5.0, "sl": 0.0},
            {"profit": 8.0, "sl": 3.0},
            {"profit": 12.0, "sl": 6.0},
        ],
        "DSLA_VOLATILITY_PERIOD": 14,
        "DSLA_VOLATILITY_MULTIPLIER": 1.5,
        "DSLA_VOLATILITY_STEPS": 3,
        "ALLOWED_COIN_TYPES": ["MAJOR", "DEFI", "ALTCOIN"],
        "DEFI_TOKEN_LIST": [
            "UNIUSDT", "AAVEUSDT", "LINKUSDT", "MKRUSDT", "SNXUSDT", "COMPUSDT",
        ],
    },
}

DEFAULT_SETTINGS = {"PROFIT_TARGET_PERCENTAGE": 1.0}
def get_active_settings(tier: str):
    return SUBSCRIPTION_TIERS.get(tier.upper(), SUBSCRIPTION_TIERS["FREE"])

# 6. --- Module Unification ---
# This hack helps ensure that both `import config` and `from . import config`
# resolve to the same module object, preventing duplicate, uninitialized instances.
# It's a workaround for inconsistencies in the project's import structure.
try:
    sys.modules.setdefault("config", sys.modules[__name__])
except Exception:
    pass
