import os

from dotenv import load_dotenv


def safe_print_config():
    """
    Prints a sanitized version of the environment variables for debugging,
    masking sensitive keys to prevent them from being exposed in logs.
    """
    print("[DEBUG] Sanitized Environment Variables:")
    safe_keys = [
        "MODE",
        "ENV",
        "VERSION",
        "CHAT_ID",
        "ADMIN_USER_ID",
        "GDRIVE_REMOTE_NAME",
        "DB_NAME",
        "AI_TRADE_INTERVAL_MINUTES",
        "TELEGRAM_SYNC_LOG_ENABLED",
        "BTC_ALERT_THRESHOLD_PERCENT",
        "HELD_TOO_LONG_HOURS",
        "NEAR_STOP_LOSS_THRESHOLD_PERCENT",
        "NEAR_TAKE_PROFIT_THRESHOLD_PERCENT",
        "RSI_BUY_RECOVERY_THRESHOLD",
        "WATCHLIST_TIMEOUT_HOURS",
        "PAPER_TRADE_SIZE_USDT",
        "PAPER_STARTING_BALANCE",
    ]
    for key, value in os.environ.items():
        # Check if the key is in the safe list or doesn't appear to be sensitive
        is_safe = key.upper() in safe_keys or (
            "API" not in key.upper()
            and "KEY" not in key.upper()
            and "TOKEN" not in key.upper()
            and "SECRET" not in key.upper()
        )

        if is_safe:
            print(f"  - {key}: {value}")
        else:
            print(f"  - {key}: **** MASKED ****")


# Explicitly load .env from the project root, regardless of working directory
# Correctly locate the .env file by going up one directory from src
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
print(f"[DEBUG] Loading .env from: {dotenv_path}")
load_dotenv(dotenv_path=dotenv_path)
REDIS_URL = os.getenv("REDIS_URL", None)  # e.g., "redis://localhost:6379/0"

# Debug: Print sanitized environment variables after loading
safe_print_config()

# For the Binance API setup
BINANCE_ENCRYPTION_KEY = (
    os.getenv("BINANCE_ENCRYPTION_KEY").encode()
    if os.getenv("BINANCE_ENCRYPTION_KEY")
    else None
)

# For the "sandpaper" data
SANDPAPER_ENCRYPTION_KEY = (
    os.getenv("SANDPAPER_ENCRYPTION_KEY").encode()
    if os.getenv("SANDPAPER_ENCRYPTION_KEY")
    else None
)

# Coins to monitor for AI trading logic
AI_MONITOR_COINS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "ADAUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "DOTUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "ARBUSDT",
    "OPUSDT",
    "LTCUSDT",
    "TRXUSDT",
    "SHIBUSDT",
    "PEPEUSDT",
    "UNIUSDT",
    "SUIUSDT",
    "INJUSDT",
    "CTKUSDT",
    "ENAUSDT",
]

# --- Per-trade allocation limit (as a percentage of available USDT balance) ---
PER_TRADE_ALLOCATION_PERCENT = 5.0  # Example: Allocate 5% of available USDT per trade

# --- Telegram Sync Log ---
TELEGRAM_SYNC_LOG_ENABLED = True  # Set to True to enable trade sync logs via Telegram
# Interval (in minutes) for the AI autotrade monitor job
AI_TRADE_INTERVAL_MINUTES = 10

# Telegram and Binance API credentials from .env file


# --- Telegram ---
# Support either TELEGRAM_BOT_TOKEN or legacy BOT_TOKEN env var (some deploys set BOT_TOKEN)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")

# --- Binance ---
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")

# --- Database ---
DB_NAME = "lunessa.db"

# --- Security ---
WEBHOOK_HMAC_SECRET = os.getenv("WEBHOOK_HMAC_SECRET")
if not WEBHOOK_HMAC_SECRET:
    print("Warning: WEBHOOK_HMAC_SECRET is not set. Webhook verification will be skipped.")
SLIP_ENCRYPTION_KEY = os.getenv("SLIP_ENCRYPTION_KEY")

# --- User Management ---
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))

# --- Subscription Tiers ---


# --- AI & Caching Configuration ---
GEMINI_API_URL = os.getenv("GEMINI_API_URL", "https://api.gemini.example/analysis")


# --- Database Configuration ---
DB_NAME = "lunara_bot.db"  # Dedicated database file for reliability

# --- Global Market & Bot Behavior Settings (Not Tier-Dependent) ---

# Strategic Alert configuration
BTC_ALERT_THRESHOLD_PERCENT = 2.0  # Alert if BTC moves more than this % in 1 hour.
HELD_TOO_LONG_HOURS = 48  # Alert if a trade is open longer than this.
NEAR_STOP_LOSS_THRESHOLD_PERCENT = 2.0  # Alert if price is within this % of stop-loss.
NEAR_TAKE_PROFIT_THRESHOLD_PERCENT = (
    2.0  # Alert if price is within this % of the take-profit target.
)

# Dip-Buying Logic
RSI_BUY_RECOVERY_THRESHOLD = (
    32.0  # Buy if RSI crosses above this after dipping below RSI_BUY_THRESHOLD.
)
WATCHLIST_TIMEOUT_HOURS = 24  # Remove from watchlist after this many hours.

# --- Paper Trading ---
PAPER_TRADE_SIZE_USDT = 1000.0
PAPER_STARTING_BALANCE = 10000.0


# --- Subscription Tier Configuration ---
# This structure allows for easy management of different user levels.
# In the future, the bot's logic will check a user's subscription
# and apply the appropriate settings. For now, we can set a global default.

SUBSCRIPTION_TIERS = {
    "FREE": {
        "NAME": "Free",
        # Basic fixed trading parameters
        "RSI_BUY_THRESHOLD": 30.0,
        "RSI_SELL_THRESHOLD": 70.0,
        "PROFIT_TARGET_PERCENTAGE": 1.0,
        "STOP_LOSS_PERCENTAGE": 5.0,
        # Feature flags for this tier
        "USE_TRAILING_TAKE_PROFIT": False,
        "USE_BOLLINGER_BANDS": False,
        "ALLOWED_COIN_TYPES": ["MAJOR"],  # e.g., only BTC, ETH, BNB
    },
    "PREMIUM": {
        "NAME": "Premium",
        # Core trading parameters (can be customized by user later)
        "RSI_BUY_THRESHOLD": 30.0,
        "RSI_SELL_THRESHOLD": 70.0,
        "PROFIT_TARGET_PERCENTAGE": 1.0,  # Default, can be overridden by dynamic logic
        "STOP_LOSS_PERCENTAGE": 4.0,  # Default, can be overridden by dynamic logic
        # --- Dynamic Logic & Advanced Features ---
        # Trailing Take Profit: Sells if price drops X% from a recent peak
        "USE_TRAILING_TAKE_PROFIT": True,
        "TRAILING_PROFIT_ACTIVATION_PERCENT": 7.0,  # Trailing activates after this % profit
        "TRAILING_STOP_DROP_PERCENT": 3.0,  # Sell if price drops this % from peak
        # Bollinger Bands (BOLL) Intelligence
        "USE_BOLLINGER_BANDS": True,
        "BOLL_PERIOD": 20,
        "BOLL_STD_DEV": 2,
        "BOLL_SQUEEZE_ALERT_ENABLED": True,
        "BOLL_SQUEEZE_THRESHOLD": 0.08,  # Alert if (Upper-Lower)/Middle band is less than this
        # Enhanced RSI Signals
        "RSI_OVERBOUGHT_ALERT_THRESHOLD": 80.0,  # Send special alert if RSI exceeds this
        "RSI_BEARISH_EXIT_THRESHOLD": 65.0,
        # --- Dynamic Stop-Loss (DSLA) ---
        "DSLA_MODE": "step_ladder",  # Other modes: 'volatility_based', 'ATR_based'
        "DSLA_LADDER": [
            {"profit": 5.0, "sl": 0.0},  # At +5% profit, move SL to breakeven
            {"profit": 8.0, "sl": 3.0},  # At +8% profit, move SL to +3%
            {"profit": 12.0, "sl": 6.0},  # At +12% profit, move SL to +6%
        ],
        # Volatility-based DSLA settings
        "DSLA_VOLATILITY_PERIOD": 14,  # ATR period for volatility calculation
        "DSLA_VOLATILITY_MULTIPLIER": 1.5,  # ATR multiplier for the first ladder step
        "DSLA_VOLATILITY_STEPS": 3,  # Number of steps in the dynamic ladder
        # DeFi & Altcoin Support
        "ALLOWED_COIN_TYPES": ["MAJOR", "DEFI", "ALTCOIN"],
        "DEFI_TOKEN_LIST": [  # Example list, can be expanded
            "UNIUSDT",
            "AAVEUSDT",
            "LINKUSDT",
            "MKRUSDT",
            "SNXUSDT",
            "COMPUSDT",
        ],
    },
}


# --- Helper function to get current settings ---
# This will be crucial for the rest of the code to adapt to the tier system.
# Default settings used as a simple canonical fallback (tests may patch this).
DEFAULT_SETTINGS = {"PROFIT_TARGET_PERCENTAGE": 1.0}
def get_active_settings(tier: str):
    """
    Returns the settings dictionary for the given subscription tier.
    """
    # Fallback to FREE tier if the configured tier is invalid
    return SUBSCRIPTION_TIERS.get(tier.upper(), SUBSCRIPTION_TIERS["FREE"])


# Ensure plain `import config` (non-package import) resolves to this module
# when the package is executed as `python -m src.main` or similar. This
# avoids duplicate module objects where some modules see a different
# `config` without attributes like ADMIN_USER_ID.
try:
    import sys

    sys.modules.setdefault("config", sys.modules[__name__])
except Exception:
    # Be conservative: if we can't mutate sys.modules for any reason, just
    # continue; other guardrails in main.py also attempt to unify modules.
    pass
