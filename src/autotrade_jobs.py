import os

# Load Gemini and Mistral API keys from environment variables
gemini_api_keys = [os.getenv("GEMINI_KEY_1"), os.getenv("GEMINI_KEY_2")]
mistral_api_key = os.getenv("MISTRAL_KEY")

import time

# Unified AI suggestion function with fallback logic
import httpx


async def get_ai_suggestions(prompt):
    # Try Gemini keys in order
    for gemini_key in gemini_api_keys:
        if not gemini_key:
            continue
        try:
            import google.generativeai as genai

            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = await model.generate_content_async(prompt)
            return response.text.strip()
        except Exception as e:
            error_msg = str(e)
            if (
                (hasattr(e, "status_code") and e.status_code == 429)
                or "429" in error_msg
                or "quota" in error_msg.lower()
            ):
                continue  # Try next Gemini key
            else:
                break  # For other errors, stop trying Gemini
    # If all Gemini keys fail, use Mistral
    if mistral_api_key:
        mistral_url = "https://api.mistral.ai/v1/chat/completions"
        mistral_headers = {
            "Authorization": f"Bearer {mistral_api_key}",
            "Content-Type": "application/json",
        }
        mistral_payload = {
            "model": "mistral-tiny",
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    mistral_url, headers=mistral_headers, json=mistral_payload
                )
                resp.raise_for_status()
                mistral_response = resp.json()
                return mistral_response["choices"][0]["message"]["content"].strip()
        except Exception as me:
            logger.error(f"Error getting Mistral batch suggestion: {me}")
    return None


import json
import logging

import redis
from telegram.ext import ContextTypes

import config

# Local imports: prefer package-relative when running as a module
if __package__:
    from . import slip_manager, trade
    from .modules import db_access as autotrade_db

    pass
else:
    import slip_manager
    import trade
    from modules import db_access as autotrade_db

    pass

logger = logging.getLogger(__name__)


async def get_trade_suggestions_from_gemini(symbols):
    suggestions = {}
    # Gather all metrics for all symbols
    metrics = {}
    for symbol in symbols:
        try:
            rsi = trade.get_rsi(symbol)
            upper_band, sma, lower_band, bb_std = trade.get_bollinger_bands(symbol)
            macd, macd_signal, macd_hist = trade.get_macd(symbol)
            micro_vwap = trade.get_micro_vwap(symbol)
            volume_ratio = trade.get_bid_ask_volume_ratio(symbol)
            mad = trade.get_mad(symbol)
            # Gracefully skip if any required metric is None (insufficient kline data)
            if None in (
                rsi,
                upper_band,
                sma,
                lower_band,
                bb_std,
                macd,
                macd_signal,
                macd_hist,
                micro_vwap,
                volume_ratio,
                mad,
            ):
                logger.info(f"Not enough kline data for {symbol}, skipping.")
                continue
            metrics[symbol] = {
                "RSI": rsi,
                "Bollinger Bands": f"upper={upper_band}, sma={sma}, lower={lower_band}, std={bb_std}",
                "MACD": f"{macd}, Signal: {macd_signal}, Histogram: {macd_hist}",
                "Micro-VWAP": micro_vwap,
                "Bid/Ask Volume Ratio": volume_ratio,
                "MAD": mad,
            }
        except Exception as e:
            logger.error(f"Error gathering metrics for {symbol}: {e}")

    # Build a single prompt for all coins
    prompt = """Analyze the current market for the following coins. For each coin, answer with only 'buy' or 'hold'.

"""
    for symbol, data in metrics.items():
        prompt += f"Symbol: {symbol}\n"
        for k, v in data.items():
            prompt += f"{k}: {v}\n"
        prompt += "\n"
    prompt += "For each symbol, should I buy now for a small gain? Answer with only 'buy' or 'hold' for each coin, in a clear list."

    ai_response = await get_ai_suggestions(prompt)
    if ai_response:
        lines = ai_response.splitlines()
        for line in lines:
            parts = line.strip().split(":")
            if len(parts) == 2:
                symbol, decision = parts[0].strip().upper(), parts[1].strip().lower()
                if symbol in metrics and decision in ["buy", "hold"]:
                    suggestions[symbol] = decision
    return suggestions


async def autotrade_cycle(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Starting autotrade cycle...")
    user_id = getattr(config, "ADMIN_USER_ID", None)

    # Skip autotrade if Binance client is not available
    try:
        current_available = bool(getattr(trade, "BINANCE_AVAILABLE", False))
        # Detect flip from unavailable -> available to notify admin
        try:
            rc = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
            prev = rc.get("autotrade:binance_prev_available")
        except Exception:
            rc = None
            prev = None

        if not current_available:
            logger.info("Skipping autotrade cycle: Binance client unavailable.")
            # Record that a cycle was skipped for auditing
            try:
                rc = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
                rc.hincrby("autotrade:stats", "skipped_cycles", 1)
                rc.lpush(
                    "autotrade:skipped_events",
                    json.dumps({"ts": time.time(), "reason": "binance_unavailable"}),
                )
                rc.ltrim("autotrade:skipped_events", 0, 999)
            except Exception:
                pass
            return
        else:
            # If we became available and prev was false, notify admin about skipped events
            try:
                if rc and (prev in (None, "0") or prev == "False"):
                    skipped = []
                    try:
                        raw = rc.lrange("autotrade:skipped_events", 0, 9)
                        for item in raw:
                            try:
                                skipped.append(json.loads(item))
                            except Exception:
                                skipped.append({"raw": item})
                    except Exception:
                        skipped = []

                    if skipped:
                        # Build a summary message
                        text = "Autotrade resumed. Skipped events while Binance was down:\n"
                        for s in skipped:
                            t = s.get("ts")
                            sym = s.get("symbol") or s.get("reason")
                            text += f"- {sym} at {t}\n"
                        try:
                            # best-effort notify admin
                            admin_id = getattr(config, "ADMIN_USER_ID", None)
                            if admin_id and context and getattr(context, "bot", None):
                                await context.bot.send_message(
                                    chat_id=admin_id, text=text
                                )
                        except Exception:
                            logger.info(
                                "Failed to notify admin about skipped autotrade events"
                            )
            except Exception:
                pass

        # Persist current availability for next cycle
        try:
            if rc:
                rc.set("autotrade:binance_prev_available", str(current_available))
        except Exception:
            pass
    except Exception:
        # If we cannot determine status, be conservative and skip
        logger.info(
            "Skipping autotrade cycle: could not determine Binance client availability."
        )
        return

    # Determine whether autotrade is enabled for this user.
    autotrade_enabled = False
    try:
        autotrade_enabled = bool(autotrade_db.get_autotrade_status(user_id))
    except Exception:
        autotrade_enabled = False

    # Allow Redis-based toggles to override or disable autotrade globally.
    try:
        rc = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
        # Global override — if explicitly set to False/0, skip.
        g = rc.get("autotrade:global")
        if g is not None and str(g).lower() in ("false", "0"):
            logger.info("Global autotrade disabled via Redis flag autotrade:global.")
            return
        # New precedence rules for autotrade toggles (clarified keys):
        # - Global Redis key: autotrade:global (can disable autotrade for everyone)
        # - For PREMIUM users: autotrade is ON by default unless an admin override exists
        #   stored at autotrade:override:<user_id>
        # - For non-PREMIUM users: a user-level preference may exist at autotrade:user:<user_id>
        #   which takes precedence over the DB setting.
        try:
            # Determine user tier from DB helper (decorated call)
            try:
                tier = autotrade_db.get_user_tier_db(user_id)
            except Exception:
                tier = None

            if tier and str(tier).upper() == "PREMIUM":
                # PREMIUM users are enabled by default unless an admin override exists
                admin_override = rc.get(f"autotrade:override:{user_id}")
                if admin_override is not None:
                    autotrade_enabled = str(admin_override).lower() in ("true", "1")
                else:
                    autotrade_enabled = True
            else:
                # Non-premium users: user preference (if present) overrides DB
                user_pref = rc.get(f"autotrade:user:{user_id}")
                if user_pref is not None:
                    autotrade_enabled = str(user_pref).lower() in ("true", "1")
                # else: keep autotrade_enabled as read from DB earlier
        except Exception:
            # If anything goes wrong reading tier or Redis, fall back to DB-only setting
            pass
    except Exception:
        # If Redis is unavailable, fall back to DB-only setting (autotrade_enabled)
        pass

    if not autotrade_enabled:
        logger.info("Autotrade is disabled (DB/Redis). Skipping cycle.")
        return

    settings = autotrade_db.get_user_effective_settings(user_id)
    trade_size = float(settings.get("TRADE_SIZE_USDT", 5.0))

    # Prioritize a short list of coins for AI analysis
    prioritized_coins = config.AI_MONITOR_COINS[:5]  # Only analyze top 5 coins
    suggestions = await get_trade_suggestions_from_gemini(prioritized_coins)

    for symbol, decision in suggestions.items():
        if decision == "buy":
            try:
                usdt_balance = trade.get_account_balance(user_id, "USDT")
                if usdt_balance is None or usdt_balance < trade_size:
                    logger.warning(
                        f"Insufficient balance to autotrade {symbol}. Current USDT: {usdt_balance:.2f}, Required: {trade_size:.2f}. Skipping buy."
                    )
                    continue

                order, entry_price, quantity = trade.place_buy_order(
                    user_id, symbol, trade_size
                )

                # Log the trade to the database
                settings = autotrade_db.get_user_effective_settings(user_id)
                stop_loss_price = entry_price * (
                    1 - settings["STOP_LOSS_PERCENTAGE"] / 100
                )
                take_profit_price = entry_price * (
                    1 + settings["PROFIT_TARGET_PERCENTAGE"] / 100
                )
                autotrade_db.log_trade(
                    user_id=user_id,
                    coin_symbol=symbol,
                    buy_price=entry_price,
                    stop_loss=stop_loss_price,
                    take_profit=take_profit_price,
                    mode="LIVE",
                    quantity=quantity,
                    trade_size_usdt=trade_size,
                )

                slip_manager.create_and_store_slip(symbol, "buy", quantity, entry_price)

                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🤖 Autotrade executed: Bought {quantity:.4f} {symbol} at ${entry_price:.8f}",
                )
            except trade.TradeError as e:
                logger.error(f"Error executing autotrade for {symbol}: {e}")
                # Record skipped autotrade event for later audit/notification
                try:
                    rc = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
                    ev = {
                        "ts": time.time(),
                        "symbol": symbol,
                        "reason": str(e),
                        "type": "autotrade_buy_failed",
                    }
                    rc.lpush("autotrade:skipped_events", json.dumps(ev))
                    rc.hincrby("autotrade:stats", "skipped_events", 1)
                    rc.ltrim("autotrade:skipped_events", 0, 999)
                except Exception:
                    pass


async def mock_autotrade_buy(
    user_id: int, symbol: str, amount: float, context: ContextTypes.DEFAULT_TYPE = None
):
    """Creates a mock autotrade buy slip tagged as sandpaper for testing end-to-end lifecycle."""
    try:
        # Use slip_manager to create and store the slip
        trade_id = slip_manager.create_and_store_slip(
            symbol=symbol, side="buy", amount=amount, price=0.0
        )
        logger.info(
            f"[MOCKBUY] Mock autotrade buy created trade_id={trade_id} symbol={symbol} amount={amount} user_id={user_id}"
        )
        if context and getattr(context, "bot", None):
            await context.bot.send_message(
                chat_id=user_id,
                text=f"[MOCKBUY] Created autotrade slip trade:{trade_id} for {symbol} x{amount}",
            )
        return trade_id
    except Exception as e:
        logger.error(f"mock_autotrade_buy failed: {e}")
        return None


async def autotrade_buy_from_suggestions(
    user_id: int,
    symbols: list = None,
    context: ContextTypes.DEFAULT_TYPE = None,
    dry_run: bool = False,
    max_create: int | None = None,
):
    """Fetch suggestions from Gemini and create mock sandpaper buys for coins recommended as 'buy'.
    Returns list of created trade_ids.
    """
    created = []
    try:
        # If symbols not provided, use prioritized list from config
        if symbols is None:
            symbols = config.AI_MONITOR_COINS[:10]

        # Try to use cached suggestions if available (gemini_cache) but import lazily
        suggestions = None
        try:
            try:
                from gemini_cache import get_suggestions_for, set_suggestions_for
            except Exception:
                get_suggestions_for = None
                set_suggestions_for = None

            if get_suggestions_for:
                cached = get_suggestions_for(symbols)
                if cached:
                    logger.info("Using cached Gemini suggestions")
                    suggestions = cached
        except Exception:
            # If cache lookup fails, continue to live fetch
            suggestions = None

        if suggestions is None:
            suggestions = await get_trade_suggestions_from_gemini(symbols)
            # store in cache if available
            try:
                if (
                    "set_suggestions_for" in locals()
                    and set_suggestions_for
                    and suggestions
                ):
                    set_suggestions_for(symbols, suggestions)
            except Exception:
                pass
        # Use user effective settings for trade size
        try:
            from autotrade_settings import get_effective_settings

            settings = get_effective_settings(user_id)
            trade_size = float(settings.get("TRADE_SIZE_USDT", 5.0))
        except Exception:
            trade_size = 5.0

        # Build ordered list of buy candidates
        buy_candidates = [s for s, d in (suggestions or {}).items() if d == "buy"]

        # If dry_run, return which symbols would be bought (respect max_create)
        if dry_run:
            if max_create is not None:
                logger.info(
                    f"[AUTOSUGGEST] DRY RUN - would create mock buys for: {buy_candidates[:max_create]}"
                )
                created.extend(buy_candidates[:max_create])
            else:
                logger.info(
                    f"[AUTOSUGGEST] DRY RUN - would create mock buys for: {buy_candidates}"
                )
                created.extend(buy_candidates)
            return created

        # Non-dry run: limit creations to max_create if provided
        if max_create is not None:
            buy_candidates = buy_candidates[:max_create]

        for symbol in buy_candidates:
            try:
                tid = await mock_autotrade_buy(user_id, symbol, trade_size, context)
                if tid:
                    created.append(str(tid))
                    logger.info(
                        f"[MOCKBUY] autotrade_buy_from_suggestions created trade {tid} for {symbol}"
                    )
            except Exception as e:
                logger.error(f"Failed to create mock buy for {symbol}: {e}")
    except Exception as e:
        logger.error(f"autotrade_buy_from_suggestions failed: {e}")

    return created


async def monitor_autotrades(
    context: ContextTypes.DEFAULT_TYPE = None, dry_run: bool = False
) -> None:
    logger.info("[MONITOR] Monitoring open autotrades...")
    # Only process keys that match the expected trade slip pattern (e.g., start with 'trade:')
    # Fetch all keys starting with 'trade:' and group them by trade id.
    try:
        raw_keys = list(slip_manager.redis_client.scan_iter("trade:*"))
    except Exception:
        # If redis isn't available, fallback to listing from slip_manager
        raw_keys = [
            k for k in slip_manager.fallback_cache.keys() if k.startswith("trade:")
        ]

    grouped = {}
    # Normalize keys to strings and group
    for raw_key in raw_keys:
        try:
            k = (
                raw_key.decode()
                if isinstance(raw_key, (bytes, bytearray))
                else str(raw_key)
            )
        except Exception:
            k = str(raw_key)
        parts = k.split(":")
        # Expect patterns like 'trade:<id>' (full slip) or 'trade:<id>:<field>' (per-field)
        if len(parts) >= 2:
            trade_id = parts[1]
            grouped.setdefault(trade_id, []).append(k)

    for trade_id, keys in grouped.items():
        try:
            # First, attempt to find a full-slip key (exactly 'trade:<id>')
            full_key = next((kk for kk in keys if kk == f"trade:{trade_id}"), None)
            slip = None
            if full_key:
                # read and expect a dict
                slip = slip_manager.get_and_decrypt_slip(
                    full_key.encode()
                    if isinstance(raw_keys[0], (bytes, bytearray))
                    else full_key
                )
            else:
                # Reconstruct slip from per-field keys
                fields = {}
                for kk in keys:
                    # kk looks like 'trade:49:quantity' or similar
                    val = slip_manager.get_and_decrypt_slip(
                        kk.encode()
                        if isinstance(raw_keys[0], (bytes, bytearray))
                        else kk
                    )
                    if val is None:
                        continue
                    # Extract field name
                    p = kk.split(":")
                    if len(p) >= 3:
                        field_name = p[2]
                        fields[field_name] = val
                # if we have symbol, price, amount combine
                if all(x in fields for x in ("symbol", "price", "quantity")):
                    slip = {
                        "symbol": fields["symbol"],
                        "price": float(fields["price"]),
                        "amount": float(fields["quantity"]),
                    }
                elif all(x in fields for x in ("symbol", "price", "amount")):
                    slip = {
                        "symbol": fields["symbol"],
                        "price": float(fields["price"]),
                        "amount": float(fields["amount"]),
                    }

            logger.info(
                f"Processing reconstructed slip for trade_id={trade_id}: {slip}"
            )
            logger.info(
                f"[MANUALMONITOR] Processing reconstructed slip for trade_id={trade_id}: {slip}"
            )
            if slip is None:
                continue

            # Only process autotrade 'sandpaper' slips here
            if not isinstance(slip, dict) or not all(
                k in slip for k in ("symbol", "price", "amount")
            ):
                continue
            if not slip.get("sandpaper"):
                logger.debug(f"[MONITOR] Skipping non-sandpaper slip: {trade_id}")
                continue

            # Get per-user effective autotrade settings
            try:
                from autotrade_settings import get_effective_settings

                admin_id = getattr(config, "ADMIN_USER_ID", None)
                settings = get_effective_settings(admin_id)
            except Exception:
                settings = {"PROFIT_TARGET_PERCENTAGE": 1.0}

            current_price = trade.get_current_price(slip["symbol"])
            if not current_price:
                continue

            pnl_percent = (
                (current_price - float(slip["price"])) / float(slip["price"])
            ) * 100
            target_pct = float(settings.get("PROFIT_TARGET_PERCENTAGE", 1.0))
            if pnl_percent >= target_pct:
                try:
                    # Dry-run mode: log the intended action but don't execute
                    if dry_run:
                        logger.info(
                            f"[MANUALMONITOR] DRY RUN - Would sell {slip['amount']} {slip['symbol']} for trade_id={trade_id} at price={current_price} (pnl={pnl_percent:.2f}%)"
                        )
                    else:
                        admin_id = getattr(config, "ADMIN_USER_ID", None)
                        trade.place_sell_order(admin_id, slip["symbol"], slip["amount"])

                    # delete all keys related to this trade (skip deletion in dry_run)
                    if dry_run:
                        logger.info(
                            f"[MANUALMONITOR] DRY RUN - Would delete keys for trade_id={trade_id}: {keys}"
                        )
                    else:
                        for kk in keys:
                            try:
                                slip_manager.delete_slip(
                                    kk.encode()
                                    if isinstance(raw_keys[0], (bytes, bytearray))
                                    else kk
                                )
                            except Exception:
                                pass

                    # Send notification via bot if available, otherwise log
                    msg_text = f"🤖 Autotrade closed: Sold {slip['amount']:.4f} {slip['symbol']} at ${current_price:.8f} for a {pnl_percent:.2f}% gain."
                    if context and getattr(context, "bot", None):
                        admin_id = getattr(config, "ADMIN_USER_ID", None)
                        if admin_id:
                            await context.bot.send_message(
                                chat_id=admin_id, text=msg_text
                            )
                        else:
                            logger.info(
                                f"[MANUALMONITOR] No admin_id configured, message: {msg_text}"
                            )
                    else:
                        logger.info(f"[MANUALMONITOR] {msg_text}")
                except Exception as trade_exc:
                    logger.error(
                        f"[MANUALMONITOR] Error placing sell order for {slip['symbol']}: {trade_exc}"
                    )
        except Exception as e:
            import traceback

            logger.error(
                f"Error monitoring autotrade for trade_id={trade_id}: {e}\n{traceback.format_exc()}"
            )


async def force_create_mock_slips(
    user_id: int,
    symbols: list,
    context: ContextTypes.DEFAULT_TYPE = None,
    max_create: int = 5,
):
    """Force-create mock buy slips for a provided list of symbols, bypassing AI suggestions.

    Returns list of created trade ids.
    """
    created = []
    try:
        if not symbols:
            return created
        to_create = symbols[:max_create]
        for symbol in to_create:
            try:
                tid = await mock_autotrade_buy(
                    user_id,
                    symbol,
                    amount=float(
                        autotrade_db.get_user_effective_settings(user_id).get(
                            "TRADE_SIZE_USDT", 5.0
                        )
                    ),
                    context=context,
                )
                if tid:
                    created.append(str(tid))
                    logger.info(f"[FORCECREATE] Created mock slip {tid} for {symbol}")
            except Exception as e:
                logger.error(
                    f"[FORCECREATE] Failed creating mock slip for {symbol}: {e}"
                )
    except Exception as e:
        logger.error(f"force_create_mock_slips failed: {e}")
    return created
