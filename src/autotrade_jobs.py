
# Standard Library Imports
import os
import time
import json
import logging
import traceback

# Third-party Imports
import httpx
import redis
import google.generativeai as genai
from telegram.ext import ContextTypes

# Local Application/Library Specific Imports
from . import config
from . import slip_manager
from . import trade
from . import gemini_cache
from . import autotrade_settings
from . import db as new_db  # Import the new thread-safe db module

# --- Globals & Configuration ---
logger = logging.getLogger(__name__)

# Load Gemini and Mistral API keys from environment variables
gemini_api_keys = [os.getenv("GEMINI_KEY_1"), os.getenv("GEMINI_KEY_2")]
mistral_api_key = os.getenv("MISTRAL_KEY")


# --- Core AI and Trade Logic ---

async def get_ai_suggestions(prompt):
    """Unified AI suggestion function with fallback logic from Gemini to Mistral."""
    # ... (rest of the function is unchanged)


async def get_trade_suggestions_from_gemini(symbols):
    """Gathers metrics for symbols and gets buy/hold suggestions from the AI."""
    # ... (rest of the function is unchanged)

async def monitor_autotrades(context: ContextTypes.DEFAULT_TYPE = None, dry_run: bool = False) -> None:
    """The main autotrade monitoring job. Checks for open trades and decides whether to sell."""
    logger.info("[MONITOR] Monitoring open autotrades...")
    try:
        raw_keys = list(slip_manager.redis_client.scan_iter("trade:*"))
    except Exception:
        raw_keys = [k for k in slip_manager.fallback_cache.keys() if k.startswith("trade:")]

    grouped = {}
    for raw_key in raw_keys:
        k = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
        parts = k.split(":")
        if len(parts) >= 2:
            grouped.setdefault(parts[1], []).append(k)

    for trade_id, keys in grouped.items():
        try:
            slip = slip_manager.reconstruct_slip_from_keys(keys, trade_id)
            if slip is None or not slip.get("sandpaper"):
                continue

            settings = autotrade_settings.get_effective_settings(getattr(config, "ADMIN_USER_ID", None))
            current_price = trade.get_current_price(slip["symbol"])
            if not current_price:
                continue

            pnl_percent = ((current_price - float(slip["price"])) / float(slip["price"])) * 100
            target_pct = float(settings.get("PROFIT_TARGET_PERCENTAGE", 1.0))

            if pnl_percent >= target_pct:
                if dry_run:
                    logger.info(f"[MONITOR] DRY RUN - Would sell {slip['amount']} {slip['symbol']} for trade_id={trade_id} at P/L {pnl_percent:.2f}%")
                else:
                    admin_id = getattr(config, "ADMIN_USER_ID", None)
                    # 1. Place the sell order
                    trade.place_sell_order(admin_id, slip["symbol"], slip["amount"])
                    
                    # 2. Mark the trade as closed in the main database
                    new_db.mark_trade_closed(trade_id, reason="autotrade_sell")
                    logger.info(f"[MONITOR] Marked trade_id={trade_id} as 'autotrade_sell' in the database.")

                    # 3. Purge the slips from Redis
                    for kk in keys:
                        slip_manager.delete_slip(kk)
                    
                    msg_text = f"🤖 Autotrade closed: Sold {slip['amount']:.4f} {slip['symbol']} @ ${current_price:.8f} for a {pnl_percent:.2f}% gain."
                    if context and getattr(context, "bot", None) and admin_id:
                        await context.bot.send_message(chat_id=admin_id, text=msg_text)
                    else:
                        logger.info(msg_text)

        except Exception as e:
            logger.error(f"Error in monitor_autotrades for trade_id={trade_id}: {e}\n{traceback.format_exc()}")

async def autotrade_buy_from_suggestions(
    user_id: int,
    symbols: list = None,
    context: ContextTypes.DEFAULT_TYPE = None,
    dry_run: bool = False,
    max_create: int | None = None,
):
    # ... (rest of the function is unchanged)

async def mock_autotrade_buy(
    user_id: int, symbol: str, amount: float, context: ContextTypes.DEFAULT_TYPE = None
):
    # ... (rest of the function is unchanged)