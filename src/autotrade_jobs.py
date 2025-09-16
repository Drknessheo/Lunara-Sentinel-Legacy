
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
from .modules import db_access as autotrade_db


# --- Globals & Configuration ---
logger = logging.getLogger(__name__)

# Load Gemini and Mistral API keys from environment variables
gemini_api_keys = [os.getenv("GEMINI_KEY_1"), os.getenv("GEMINI_KEY_2")]
mistral_api_key = os.getenv("MISTRAL_KEY")


# --- Core AI and Trade Logic ---

async def get_ai_suggestions(prompt):
    """Unified AI suggestion function with fallback logic from Gemini to Mistral."""
    # Try Gemini keys in order
    for gemini_key in gemini_api_keys:
        if not gemini_key:
            continue
        try:
            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = await model.generate_content_async(prompt)
            return response.text.strip()
        except Exception as e:
            error_msg = str(e)
            if ("429" in error_msg) or ("quota" in error_msg.lower()):
                logger.warning(f"Gemini key quota exceeded. Trying next key.")
                continue
            else:
                logger.error(f"Gemini API error: {e}")
                break  # For other errors, stop trying Gemini

    # If all Gemini keys fail, use Mistral
    if mistral_api_key:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.mistral.ai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {mistral_api_key}", "Content-Type": "application/json"},
                    json={"model": "mistral-tiny", "messages": [{"role": "user", "content": prompt}]},
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as me:
            logger.error(f"Mistral API error after Gemini fallback: {me}")

    return None


async def get_trade_suggestions_from_gemini(symbols):
    """Gathers metrics for symbols and gets buy/hold suggestions from the AI."""
    suggestions = {}
    metrics = {}
    for symbol in symbols:
        try:
            # Gracefully skip if any required metric is None
            all_metrics = {
                "RSI": trade.get_rsi(symbol),
                "Bollinger Bands": trade.get_bollinger_bands(symbol),
                "MACD": trade.get_macd(symbol),
                "Micro-VWAP": trade.get_micro_vwap(symbol),
                "Bid/Ask Volume Ratio": trade.get_bid_ask_volume_ratio(symbol),
                "MAD": trade.get_mad(symbol),
            }
            if any(v is None for v in all_metrics.values()):
                logger.info(f"Insufficient kline data for {symbol}, skipping.")
                continue
            metrics[symbol] = all_metrics
        except Exception as e:
            logger.error(f"Error gathering metrics for {symbol}: {e}")

    if not metrics:
        return {}

    prompt = "Analyze the current market for the following coins. For each coin, answer with only 'buy' or 'hold'.\n\n"
    for symbol, data in metrics.items():
        prompt += f"Symbol: {symbol}\n"
        for k, v in data.items():
            prompt += f"{k}: {v}\n"
        prompt += "\n"
    prompt += "For each symbol, should I buy now for a small gain? Answer with only 'buy' or 'hold' for each coin, in a clear list."

    ai_response = await get_ai_suggestions(prompt)
    if ai_response:
        for line in ai_response.splitlines():
            parts = line.strip().split(":")
            if len(parts) == 2:
                symbol, decision = parts[0].strip().upper(), parts[1].strip().lower()
                if symbol in metrics and decision in ["buy", "hold"]:
                    suggestions[symbol] = decision
    return suggestions


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
                    trade.place_sell_order(admin_id, slip["symbol"], slip["amount"])
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
    """Fetches AI suggestions and creates mock 'sandpaper' trades for testing."""
    created = []
    try:
        symbols = symbols or config.AI_MONITOR_COINS[:10]
        suggestions = gemini_cache.get_suggestions_for(symbols)
        if not suggestions:
            suggestions = await get_trade_suggestions_from_gemini(symbols)
            if suggestions:
                gemini_cache.set_suggestions_for(symbols, suggestions)

        settings = autotrade_settings.get_effective_settings(user_id)
        trade_size = float(settings.get("TRADE_SIZE_USDT", 5.0))
        
        buy_candidates = [s for s, d in (suggestions or {}).items() if d == "buy"]
        if max_create is not None:
            buy_candidates = buy_candidates[:max_create]

        if dry_run:
            logger.info(f"[AUTOSUGGEST] DRY RUN - would create mock buys for: {buy_candidates}")
            return buy_candidates

        for symbol in buy_candidates:
            tid = await mock_autotrade_buy(user_id, symbol, trade_size, context)
            if tid:
                created.append(str(tid))
    except Exception as e:
        logger.error(f"autotrade_buy_from_suggestions failed: {e}")
    return created

async def mock_autotrade_buy(
    user_id: int, symbol: str, amount: float, context: ContextTypes.DEFAULT_TYPE = None
):
    """Creates a mock autotrade buy slip for testing."""
    try:
        trade_id = slip_manager.create_and_store_slip(symbol=symbol, side="buy", amount=amount, price=0.0)
        logger.info(f"[MOCKBUY] Mock autotrade buy created trade_id={trade_id} for {symbol}")
        if context and getattr(context, "bot", None):
            await context.bot.send_message(chat_id=user_id, text=f"[MOCKBUY] Created slip trade:{trade_id} for {symbol}")
        return trade_id
    except Exception as e:
        logger.error(f"mock_autotrade_buy failed: {e}")
        return None
