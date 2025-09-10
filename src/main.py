import asyncio
import csv
import io
import json
import logging
import os
import sys
import time

# --- Setup logging and path ---
# This should be the very first thing to run
if __package__:
    from . import logging_config
else:
    import logging_config

logging_config.setup_logging()

print("📍 Entered main.py — after imports")

import redis

# Ensure the src directory is on sys.path so imports work when running as a script
if not __package__:
    # only modify sys.path for script mode; when running as a module the package
    # import machinery should be used to resolve relative imports correctly.
    sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
else:
    # Ensure project root is available on sys.path so top-level modules like
    # `security.py` (located at the repo root) can be imported when running
    # `python -m src.main`.
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

from datetime import datetime, timedelta, timezone

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import Conflict as TelegramConflict
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Import config first to ensure a single canonical module object is
# created and available to other modules that use a plain `import config`.
if __package__:
    from . import autotrade_jobs, config, redis_validator, slip_manager
else:
    import autotrade_jobs
    import config
    import redis_validator
    import slip_manager

# Ensure modules that use a plain `import config` (non-relative) get the
# same module object as the package-local `config` module. This avoids
# duplicated module state when running as `python -m src.main`.
try:
    import sys as _sys

    # Only set when we have a config name in this namespace
    if "config" in globals():
        _sys.modules["config"] = config
except Exception:
    # Best-effort; do not fail startup on sys.modules manipulation
    pass

# Expose a module-level admin id which is safe to reference across this module.
# Use getattr to avoid AttributeError if config was loaded differently.
ADMIN_ID = getattr(config, "ADMIN_USER_ID", None)

# Local modules: prefer package-relative imports when running as a module
# (python -m src.main). Fall back to top-level imports to preserve
# convenience when running as a script (python src/main.py).
if __package__:
    from . import trade, trade_executor
    from .modules import db_access as db
    from .redis_persistence import RedisPersistence
    from .Simulation import resonance_engine
    from .slip_parser import SlipParseError, parse_slip
else:
    import trade
    import trade_executor
    from modules import db_access as db
    from Simulation import resonance_engine
    from slip_parser import SlipParseError, parse_slip

logger = logging.getLogger(__name__)


def snip(value, limit=120):
    """Return a single-line, truncated representation of value (max `limit` chars)."""
    try:
        if value is None:
            return ""
        s = str(value)
        s = s.replace("\n", " ").replace("\r", " ")
        if len(s) <= limit:
            return s
        return s[: limit - 3] + "..."
    except Exception:
        return ""


# --- Promotion webhook helpers (module-level) ---
def update_retry_metrics(field: str, delta: int = 1):
    try:
        rc = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
        if field in ("pending", "failed", "total_sent"):
            rc.hincrby("autotrade:stats", field, delta)
            if field == "failed" and delta > 0:
                rc.hset(
                    "autotrade:stats",
                    "last_failed_ts",
                    datetime.now(timezone.utc).isoformat(),
                )
        else:
            rc.hset("autotrade:stats", field, delta)
    except Exception as e:
        logger.debug(f"Failed to update retry metrics: {e}")


def enqueue_promotion_retry(
    payload: dict, error: str = None, attempts: int = 0, next_try: float = None
) -> None:
    try:
        rc = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
        item = {
            "payload": payload,
            "attempts": int(attempts or 0),
            "last_error": str(error) if error else None,
            "next_try": float(next_try) if next_try else time.time(),
        }
        rc.rpush("autotrade:retry", json.dumps(item))
        rc.ltrim("autotrade:retry", 0, 4999)
        logger.info(
            "Enqueued promotion webhook retry (attempts=%d) for audit=%s",
            item["attempts"],
            payload.get("audit_id"),
        )
        # Increment pending counter directly to ensure the metric exists
        try:
            rc.hincrby("autotrade:stats", "pending", 1)
        except Exception:
            try:
                rc.hset("autotrade:stats", "pending", 1)
            except Exception:
                pass
        # debug marker to help tests and debugging know enqueue ran
        try:
            rc.set("autotrade:called", str(time.time()))
        except Exception:
            pass
    except Exception as e:
        logger.debug(f"Failed to enqueue promotion retry: {e}")


def dispatch_promotion_webhook_sync(
    payload: dict, webhook_url: str | None = None, timeout: int = 3
) -> tuple:
    try:
        headers = {}
        secret = getattr(config, "PROMOTION_WEBHOOK_SECRET", None)
        if secret:
            try:
                import hashlib
                import hmac

                sig = hmac.new(
                    secret.encode() if isinstance(secret, str) else secret,
                    msg=json.dumps(payload).encode("utf-8"),
                    digestmod=hashlib.sha256,
                ).hexdigest()
                headers["X-Signature"] = sig
            except Exception as ex:
                logger.debug(f"Failed to compute HMAC signature: {ex}")
        url = webhook_url or getattr(config, "PROMOTION_WEBHOOK_URL", None)
        if not url:
            return False, None, "no webhook url configured"
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        try:
            body = resp.text
        except Exception:
            body = ""
        return resp.ok, resp.status_code, body
    except Exception as ex:
        return False, None, str(ex)


def send_promotion_webhook(
    payload: dict, webhook_url: str | None = None, timeout: int = 3
) -> tuple:
    success, status, body = dispatch_promotion_webhook_sync(
        payload, webhook_url=webhook_url, timeout=timeout
    )
    if not success:
        try:
            enqueue_promotion_retry(
                payload,
                error=body or (f"http:{status}" if status else "error"),
                attempts=0,
            )
        except Exception:
            logger.debug("Failed to enqueue failed webhook from send_promotion_webhook")
    return success, status, body


# Gemini API keys are now managed in autotrade_jobs.py for multi-key support and fallback


async def redis_check_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Checks Redis connectivity and basic set/get operation."""
    try:
        import redis

        redis_client = redis.from_url(config.REDIS_URL, decode_responses=True)
        redis_client.set("healthcheck", "ok")
        value = redis_client.get("healthcheck")
        await update.message.reply_text(f"Redis is working: {value}")
    except Exception as e:
        logger.error(f"Redis check failed: {e}")
        await update.message.reply_text("Redis connection failed.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message and registers the user if they are new."""
    user = update.effective_user
    # Ensure user is in the DB, creating them with default settings if new
    db.get_or_create_user_db(user.id)

    logger.info(f"User {user.id} ({user.username}) started the bot.")

    welcome_message = (
        f"🌑 <b>A new trader emerges from the shadows.</b> {user.mention_html()}, you have been summoned by <b>Lunessa Shai'ra Gork</b>, Sorceress of DeFi and guardian of RSI gates.\n\n"
        f"Your journey begins now. I will monitor the markets for you, alert you to opportunities, and manage your trades.\n\n"
        f"<b>Key Commands:</b>\n/quest <code>SYMBOL</code> - Analyze a cryptocurrency.\n/status - View your open trades and watchlist.\n/help - See all available commands.\n\n"
        f"To unlock live trading, please provide your Binance API keys using the <code>/setapi</code> command in a private message with me."
    )

    await update.message.reply_html(welcome_message)


# TODO: In /status, alert user about market position, best moves, or when the user might hit a target time. If a position is held too long, alert to sell near stop loss, and suggest trailing stop activation. The bot should help give the user better options.
async def send_daily_status_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a daily summary of open trades to active users."""
    logger.info("Running daily status summary job...")
    all_user_ids = db.get_all_user_ids()

    # --- Send admin a user count summary ---
    try:
        admin_id = getattr(config, "ADMIN_USER_ID", None)
        if admin_id:
            user_count = len(all_user_ids)
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"👥 Total users: <b>{user_count}</b>",
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        logger.error(f"Failed to send user count to admin: {e}")

    for user_id in all_user_ids:
        open_trades = db.get_open_trades(user_id)
        if not open_trades:
            continue  # Skip users with no open trades

        # ...existing code...


async def quest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for the /crypto command. Calls the trade module."""
    user_id = update.effective_user.id
    user_tier = db.get_user_tier_db(user_id)
    if user_tier != "PREMIUM":
        # Free users: Only show RSI
        symbol = context.args[0].upper() if context.args else None
        if not symbol:
            await update.message.reply_text(
                "Please specify a symbol. Usage: /quest SYMBOL", parse_mode="Markdown"
            )
            return
        rsi = trade.get_rsi(symbol)
        if rsi is None:
            await update.message.reply_text(f"Could not fetch RSI for {symbol}.")
            return
        await update.message.reply_text(
            f"RSI for {symbol}: `{rsi:.2f}`\nUpgrade to Premium for full analysis.",
            parse_mode="Markdown",
        )
        return
    # Premium: Full analysis
    await trade.quest_command(update, context)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for the /status command. Shows subscription status, open quests, and watched symbols."""
    user_id = update.effective_user.id

    # --- Subscription Status ---
    tier, expires_str = db.get_user_subscription_db(user_id)
    autotrade_status = "✅ Enabled" if db.get_autotrade_status(user_id) else "❌ Disabled"

    subscription_message = f"👤 **Subscription Status**\n- Tier: **{tier.capitalize()}**\n- Auto-trade: {autotrade_status}\n"

    if tier != "FREE" and expires_str:
        try:
            expires_dt = datetime.fromisoformat(expires_str).astimezone(timezone.utc)
            now_utc = datetime.now(timezone.utc)
            if expires_dt > now_utc:
                days_remaining = (expires_dt - now_utc).days
                expiry_date_formatted = expires_dt.strftime("%d %b %Y")
                subscription_message += f"- Expires: **{expiry_date_formatted}** ({days_remaining} days left)\n"
            else:
                subscription_message += "- Status: **Expired**\n"
        except (ValueError, TypeError):
            subscription_message += "- Expiry: *Not set*\n"  # Handle parsing errors

    subscription_message += "\n" + ("-" * 20) + "\n\n"

    open_trades = db.get_open_trades(user_id)
    watched_items = db.get_watched_items_by_user(user_id)

    user_tier = db.get_user_tier_db(user_id)

    # Get active slips from Redis
    active_slips = slip_manager.list_all_slips()

    # Log and skip malformed slips to avoid runtime crashes (some slips may be floats or strings)
    valid_slips = []
    for slip in active_slips:
        if not isinstance(slip, dict):
            logger.warning(f"Malformed slip (not dict): {slip}")
            continue
        data = slip.get("data")
        if not isinstance(data, dict):
            logger.warning(
                f"Malformed slip data (not dict) for key={slip.get('key')}: {data}"
            )
            continue
        if "symbol" not in data:
            logger.debug(
                f"Slip missing 'symbol' field for key={slip.get('key')}: {data}"
            )
            continue
        valid_slips.append(slip)

    active_slip_symbols = {slip["data"]["symbol"] for slip in valid_slips}
    {slip["key"] for slip in valid_slips}

    # Filter open_trades to only include those actively monitored by Redis slips
    monitored_trades = [
        trade_item
        for trade_item in open_trades
        if trade_item["coin_symbol"] in active_slip_symbols
    ]
    orphaned_trades = [
        trade_item
        for trade_item in open_trades
        if trade_item["coin_symbol"] not in active_slip_symbols
    ]

    if not monitored_trades and not watched_items and not orphaned_trades:
        # Prepend subscription status even if there are no trades
        await update.message.reply_text(
            subscription_message
            + "You have no open quests, watched symbols, or orphaned trades. Use /quest to find an opportunity.",
            parse_mode="Markdown",
        )
        return

    message = ""

    # --- Get all prices from the job's cache ---
    prices = {}
    cached_prices_data = context.bot_data.get("all_prices", {})
    if cached_prices_data:
        cache_timestamp = cached_prices_data.get("timestamp")
        # Cache is valid if it's less than 125 seconds old (job runs every 60s)
        if (
            cache_timestamp
            and (datetime.now(timezone.utc) - cache_timestamp).total_seconds() < 125
        ):
            prices = cached_prices_data.get("prices", {})
            logger.info(f"Using cached prices for /status for user {user_id}.")
        else:
            logger.warning(
                f"Price cache for user {user_id} is stale. Displaying last known data."
            )

    if monitored_trades:
        message += "**Your Open Quests (Monitored):**\n"
        for trade_item in monitored_trades:
            symbol = trade_item["coin_symbol"]
            buy_price = trade_item["buy_price"]
            current_price = prices.get(symbol)
            trade_id = trade_item["id"]

            message += f"\n🔹 **{symbol}** (ID: {trade_id})"

            if current_price:
                pnl_percent = ((current_price - buy_price) / buy_price) * 100
                pnl_emoji = "📈" if pnl_percent >= 0 else "📉"
                message += (
                    f"\n   {pnl_emoji} P/L: `{pnl_percent:+.2f}%`"
                    f"\n   Bought: `${buy_price:,.8f}`"
                    f"\n   Current: `${current_price:,.8f}`"
                )
                if user_tier == "PREMIUM":
                    tp_price = trade_item["take_profit_price"]
                    stop_loss = trade_item["stop_loss_price"]
                    message += (
                        f"\n   ✅ Target: `${tp_price:,.8f}`"
                        f"\n   🛡️ Stop: `${stop_loss:,.8f}`"
                    )
            else:
                message += "\n   _(Price data is currently being updated)_"

        message += "\n"  # Add a newline for spacing before the watchlist

    if orphaned_trades:
        message += "⚠️ **Orphaned Quests (Not Monitored by Redis):**\n"
        message += "_These trades are in your database but not actively monitored by the bot. Consider closing them manually if they are no longer active._\n"
        for trade_item in orphaned_trades:
            symbol = trade_item["coin_symbol"]
            trade_id = trade_item["id"]
            message += f"\n🔸 **{symbol}** (ID: {trade_id})"
        message += "\n"  # Add a newline for spacing

    if watched_items:
        message += "\n🔭 **Your Watched Symbols:**\n"
        for item in watched_items:
            # Calculate time since added
            add_time = datetime.strptime(
                item["add_timestamp"], "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
            time_watching = datetime.now(timezone.utc) - add_time
            hours, remainder = divmod(time_watching.total_seconds(), 3600)
            minutes, _ = divmod(remainder, 60)
            message += f"\n🔸 **{item['coin_symbol']}** (*Watching for {int(hours)}h {int(minutes)}m*)"

    # The send_premium_message wrapper is overly complex; a direct reply is cleaner.
    await update.message.reply_text(
        subscription_message + message, parse_mode="Markdown"
    )


async def resonate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs LunessaSignals's quantum resonance simulation and sends the results."""
    user_id = update.effective_user.id
    symbol = None
    if context.args:
        symbol = context.args[0].upper()
        await update.message.reply_text(
            f"Attuning my quantum senses to the vibrations of **{symbol}**... Please wait. 🔮",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await update.message.reply_text(
            "Attuning my quantum senses to the general market vibration... Please wait. 🔮"
        )

    metric_plot_path = None
    clock_plot_path = None
    try:
        # Run the potentially long-running simulation in a separate thread
        # to avoid blocking the bot's event loop.
        # Pass the symbol to the simulation engine.
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None, resonance_engine.run_resonance_simulation, user_id, symbol
        )

        narrative = results["narrative"]
        metric_plot_path = results["metric_plot"]
        clock_plot_path = results["clock_plot"]

        # Send the narrative text
        await update.message.reply_text(narrative, parse_mode=ParseMode.MARKDOWN)

        # Send the plots
        with open(metric_plot_path, "rb") as photo1, open(
            clock_plot_path, "rb"
        ) as photo2:
            await update.message.reply_photo(
                photo=photo1, caption="Soul Waveform Analysis"
            )
            await update.message.reply_photo(
                photo=photo2, caption="Clock Phase Distortions"
            )

    except Exception as e:
        logger.error(
            f"Error running resonance simulation for user {user_id}: {e}", exc_info=True
        )
        await update.message.reply_text(
            "The cosmic energies are scrambled. I could not generate a resonance report at this time."
        )
    finally:
        # Clean up the generated plot files
        if metric_plot_path and os.path.exists(metric_plot_path):
            os.remove(metric_plot_path)
        if clock_plot_path and os.path.exists(clock_plot_path):
            os.remove(clock_plot_path)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/settings handler wired to `autotrade_settings` for validation and storage.

    Usage:
      /settings <key> <value>
      /settings <key> reset
      /settings export
    """
    try:
        from autotrade_settings import (
            KEY_DEFINITIONS,
            export_settings_csv,
            validate_and_set,
        )
    except Exception as e:
        logger.error("Failed to load autotrade_settings: %s", e)
        await update.message.reply_text("Settings subsystem unavailable.")
        return

    user_id = update.effective_user.id
    # Only admin is allowed to change global settings in this implementation
    if user_id != ADMIN_ID:
        await update.message.reply_text(
            "Only the admin can change autotrade settings via this command."
        )
        return

    if not context.args:
        # Show available keys and usage
        keys_list = "\n".join(
            [
                f"`{k}` - {v['name']} (default={v['default']}, range={v.get('min')}-{v.get('max')})"
                for k, v in KEY_DEFINITIONS.items()
            ]
        )
        await update.message.reply_text(
            "Usage:\n/settings <key> <value>\n/settings <key> reset\n/settings export\n\nAvailable settings:\n"
            + keys_list,
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # export command
    if len(context.args) == 1 and context.args[0].lower() == "export":
        csv_text = export_settings_csv(user_id)
        # Send small CSV as a file if > 4000 chars otherwise inline
        if len(csv_text) > 3500:
            await update.message.reply_document(
                document=csv_text.encode("utf-8"), filename="settings.csv"
            )
        else:
            await update.message.reply_text(
                f"CSV:\n<pre>{csv_text}</pre>", parse_mode=ParseMode.HTML
            )
        return

    # Expect pairs: key value (or key reset)
    if len(context.args) < 2:
        await update.message.reply_text("Invalid usage. Example: /settings rsi_buy 35")
        return

    key = context.args[0]
    raw_value = " ".join(context.args[1:])

    success, msg = validate_and_set(user_id, key, raw_value, admin_scope=True)
    if success:
        await update.message.reply_text(f"✅ {msg}")
    else:
        await update.message.reply_text(f"❗ {msg}")
        return

    try:
        from autotrade_settings import get_user_settings

        current = get_user_settings(user_id) or {}
        logger.info(f"Settings updated for user {user_id}: {current}")
    except Exception as e:
        logger.error(f"Settings saved but failed to read back for logging: {e}")


async def mockbuy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only command to create a mock autotrade buy for testing the lifecycle.
    Usage: /mockbuy SYMBOL AMOUNT
    """
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("Only the admin can run this command.")
        return

    try:
        symbol = context.args[0].upper()
        amount = float(context.args[1])
    except Exception:
        await update.message.reply_text("Usage: /mockbuy SYMBOL AMOUNT")
        return

    try:
        # Call the mock buy helper
        from autotrade_jobs import mock_autotrade_buy

        trade_id = await mock_autotrade_buy(user_id, symbol, amount, context)
        if trade_id:
            await update.message.reply_text(
                f"Mock buy created: trade:{trade_id} for {symbol} x{amount}"
            )
        else:
            await update.message.reply_text("Failed to create mock buy. See logs.")
    except Exception as e:
        logger.error(f"/mockbuy failed: {e}")
        await update.message.reply_text("Mock buy failed. See logs.")


async def autosuggest_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Admin-only: fetch Gemini suggestions and create mock sandpaper buys for recommended coins."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("Only the admin can run this command.")
        return

    # default to dry-run; pass 'commit' argument to actually create slips
    commit = len(context.args) and context.args[0].lower() == "commit"
    if commit:
        await update.message.reply_text(
            "Fetching suggestions and creating mock buys (commit=true)... This may take a few seconds."
        )
    else:
        await update.message.reply_text(
            "Fetching suggestions (dry-run). Reply with /autosuggest commit to actually create mock buys."
        )

    try:
        from autotrade_jobs import autotrade_buy_from_suggestions

        # Try to get cache age for display
        cache_age = None
        try:
            from gemini_cache import get_cache_age

            cache_age = get_cache_age(config.AI_MONITOR_COINS[:10])
        except Exception:
            cache_age = None

        # If commit requested, ask for confirmation with a max-create preview
        MAX_CREATE = 5
        if commit:
            # Ask for confirmation before creating up to MAX_CREATE slips
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            f"Confirm create up to {MAX_CREATE}",
                            callback_data=f"autosuggest_confirm:{MAX_CREATE}",
                        ),
                        InlineKeyboardButton(
                            "Cancel", callback_data="autosuggest_cancel"
                        ),
                    ]
                ]
            )
            await update.message.reply_text(
                f"You requested commit. This will create up to {MAX_CREATE} mock slips. Confirm?",
                reply_markup=keyboard,
            )
            return

        created = await autotrade_buy_from_suggestions(
            user_id, None, context, dry_run=True, max_create=MAX_CREATE
        )
        if not created:
            if cache_age is not None:
                await update.message.reply_text(
                    f"No buy suggestions found. Cache age: ~{int(cache_age)}s. Fetching fresh data may help."
                )
            else:
                await update.message.reply_text(
                    "No buy suggestions found or creation failed. Check logs."
                )
            return

        await update.message.reply_text(
            f"Dry-run results - top suggested buys (preview max {MAX_CREATE}): {', '.join(created)}"
        )
    except Exception as e:
        logger.error(f"/autosuggest failed: {e}")
        await update.message.reply_text("Autosuggest failed. See logs.")


async def autosuggest_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Callback handler for autosuggest confirmation inline buttons."""
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if data.startswith("autosuggest_confirm:"):
        try:
            _, max_create = data.split(":")
            max_create = int(max_create)
        except Exception:
            max_create = 5
        # Proceed with actual creation, limited
        try:
            # Audit: record who confirmed the autosuggest commit and when
            try:
                redis_client = redis.from_url(config.REDIS_URL, decode_responses=True)
                auditor = {
                    "admin_id": (
                        query.from_user.id
                        if getattr(query, "from_user", None)
                        else None
                    ),
                    "action": "autosuggest_confirm",
                    "max_create": int(max_create),
                    "timestamp": datetime.utcnow().isoformat(),
                    "message_id": getattr(query.message, "message_id", None),
                }
                # push to a list for audit history and set last metadata
                try:
                    redis_client.lpush("autosuggest_audit", json.dumps(auditor))
                    redis_client.set("autosuggest:last", json.dumps(auditor))
                except Exception as _e:
                    logger.warning(
                        f"[AUDIT] Failed to write autosuggest audit to Redis: {_e}"
                    )
            except Exception as _e:
                logger.warning(f"[AUDIT] Redis unavailable for autosuggest audit: {_e}")

            from autotrade_jobs import autotrade_buy_from_suggestions

            created = await autotrade_buy_from_suggestions(
                getattr(config, "ADMIN_USER_ID", None),
                None,
                context,
                dry_run=False,
                max_create=max_create,
            )
            # Write a final audit entry including created trade ids and result
            try:
                try:
                    redis_client = redis.from_url(
                        config.REDIS_URL, decode_responses=True
                    )
                    final_auditor = {
                        "admin_id": (
                            query.from_user.id
                            if getattr(query, "from_user", None)
                            else None
                        ),
                        "action": "autosuggest_confirm",
                        "max_create": int(max_create),
                        "timestamp": datetime.utcnow().isoformat(),
                        "message_id": getattr(query.message, "message_id", None),
                        "created_trades": created or [],
                        "result": "created" if created else "no_created",
                    }
                    try:
                        redis_client.lpush(
                            "autosuggest_audit", json.dumps(final_auditor)
                        )
                        redis_client.set("autosuggest:last", json.dumps(final_auditor))
                    except Exception as _e:
                        logger.warning(
                            f"[AUDIT] Failed to write final autosuggest audit to Redis: {_e}"
                        )
                except Exception as _e:
                    logger.warning(
                        f"[AUDIT] Redis unavailable for final autosuggest audit: {_e}"
                    )
            except Exception:
                # Don't let audit failures block user feedback
                pass

            if created:
                await query.edit_message_text(
                    f"Created mock trades (ids): {', '.join(created)}"
                )
            else:
                await query.edit_message_text("No trades created. Check logs.")
        except Exception as e:
            logger.error(f"autosuggest confirmation failed: {e}")
            await query.edit_message_text("Failed to create mock trades. See logs.")
    else:
        # Cancel
        await query.edit_message_text("Autosuggest commit cancelled.")


async def list_sandpaper_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Admin-only: list current sandpaper slips stored in Redis for debugging."""
    user_id = update.effective_user.id
    if user_id != getattr(config, "ADMIN_USER_ID", None):
        await update.message.reply_text("Only the admin can run this command.")
        return

    try:
        slips = slip_manager.list_all_slips()
        sandpaper = [
            s
            for s in slips
            if isinstance(s.get("data", {}), dict) and s["data"].get("sandpaper")
        ]
        if not sandpaper:
            await update.message.reply_text("No sandpaper slips found.")
            return
        msg = "Current sandpaper slips:\n"
        for s in sandpaper:
            key = s.get("key")
            data = s.get("data")
            msg += f"- {key}: {data}\n"
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"list_sandpaper_command failed: {e}")
        await update.message.reply_text("Failed to list sandpaper slips. See logs.")


async def audit_recent_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Admin-only: show recent autosuggest audit entries from Redis."""
    user_id = update.effective_user.id
    if user_id != getattr(config, "ADMIN_USER_ID", None):
        await update.message.reply_text("Only the admin can view audit entries.")
        return

    # How many entries to show (default 5). Cap at 50 for safety.
    try:
        n = int(context.args[0]) if context.args else 5
    except Exception:
        n = 5
    n = max(1, min(50, n))

    try:
        redis_client = redis.from_url(config.REDIS_URL, decode_responses=True)
        items = redis_client.lrange("autosuggest_audit", 0, n - 1) or []
        if not items:
            await update.message.reply_text("No audit entries found.")
            return
        # Attempt to map admin IDs to usernames via bot API (cached)
        id_to_name = {}
        unique_ids = set()
        parsed = []
        for raw in items:
            try:
                obj = json.loads(raw)
            except Exception:
                parsed.append({"raw": raw})
                continue
            admin_id = obj.get("admin_id")
            if admin_id is not None:
                unique_ids.add(admin_id)
            parsed.append(obj)

        for aid in list(unique_ids):
            try:
                # Try Telegram API first (works if bot can access the user/chat)
                if context and getattr(context, "bot", None):
                    try:
                        chat = await context.bot.get_chat(aid)
                        name = f"@{getattr(chat, 'username', None) or getattr(chat, 'first_name', str(aid))}"
                        id_to_name[aid] = name
                        continue
                    except Exception:
                        pass
                # Fallback to configured ADMIN_ID if present
                id_to_name[aid] = getattr(config, "ADMIN_ID", str(aid)) or str(aid)
            except Exception:
                id_to_name[aid] = str(aid)

        # Build message lines
        msg_lines = [f"Recent {len(items)} autosuggest audit entries:"]
        from datetime import datetime as _dt

        for obj in parsed:
            if "raw" in obj:
                msg_lines.append(f'- RAW: {obj["raw"]}')
                continue
            ts_raw = obj.get("timestamp")
            try:
                if ts_raw:
                    try:
                        ts = _dt.fromisoformat(ts_raw)
                        ts_fmt = ts.strftime("%Y-%m-%d %H:%M:%S") + " UTC"
                    except Exception:
                        ts_fmt = ts_raw
                else:
                    ts_fmt = "unknown"
            except Exception:
                ts_fmt = str(ts_raw)

            admin = obj.get("admin_id")
            admin_name = id_to_name.get(admin, str(admin))
            result = obj.get("result", "unknown")
            created = obj.get("created_trades")
            created_display = (
                ",".join(created)
                if isinstance(created, list) and created
                else str(created)
            )
            msg_lines.append(
                f"- {ts_fmt} by {admin_name} result={result} created={created_display}"
            )

        # Telegram has message size limits; truncate if necessary
        out = "\n".join(msg_lines)
        if len(out) > 3500:
            out = out[:3490] + "\n...truncated..."
        await update.message.reply_text(out)
    except Exception as e:
        logger.error(f"audit_recent_command failed: {e}")
        await update.message.reply_text("Failed to read audit entries. See logs.")


async def safety_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Static handler for the /safety command."""
    await update.message.reply_text(
        "Protect your capital like a sacred treasure. Never invest more than you are willing to lose. "
        "A stop-loss is your shield in the volatile realm of crypto."
    )


async def hubspeedy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Static handler for the /hubspeedy command."""
    await update.message.reply_text(
        "For more advanced tools and community, check out our main application! [Link Here]"
    )


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for the /balance command. Calls the trade module."""
    await trade.balance_command(update, context)


async def close_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Closes an open trade. Usage: /close <trade_id>"""
    user_id = update.effective_user.id
    try:
        trade_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text(
            "Please provide a valid trade ID.\nUsage: `/close <trade_id>`",
            parse_mode="Markdown",
        )
        return

    trade_to_close = db.get_trade_by_id(trade_id=trade_id, user_id=user_id)

    if not trade_to_close:
        await update.message.reply_text(
            "Could not find an open trade with that ID under your name. Check `/status`.",
            parse_mode="Markdown",
        )
        return

    symbol = trade_to_close["coin_symbol"]
    buy_price = trade_to_close["buy_price"]
    current_price = trade.get_current_price(symbol)
    if current_price is None:
        await update.message.reply_text(
            f"Could not fetch the current price for {symbol} to close the trade. Please try again."
        )
        return

    pnl_percentage = ((current_price - buy_price) / buy_price) * 100
    win_loss = (
        "win" if pnl_percentage > 0 else "loss" if pnl_percentage < 0 else "breakeven"
    )
    close_reason = "manual"
    closed_by = update.effective_user.username or update.effective_user.first_name

    success = db.close_trade(
        trade_id=trade_id,
        user_id=user_id,
        sell_price=current_price,
        close_reason=close_reason,
        win_loss=win_loss,
        pnl_percentage=pnl_percentage,
        closed_by=closed_by,
    )

    if success:
        await update.message.reply_text(
            f"Trade #{trade_id} closed by @{closed_by}\n"
            f"Reason: {close_reason}\n"
            f"Result: {win_loss} ({pnl_percentage:.2f}%)"
        )
        # Attempt slip cleanup for this trade
        try:
            slip_key = None
            # Try to find the slip key by symbol (as used in slip_manager)
            active_slips = slip_manager.list_all_slips()
            for slip in active_slips:
                data = slip.get("data", {})
                if data.get("symbol") == symbol:
                    slip_key = slip["key"]
                    break
            if slip_key:
                slip_manager.cleanup_slip(slip_key)
                # Attempt to decrement any pending counters related to this trade
                try:
                    rc = None
                    if os.getenv("REDIS_URL"):
                        rc = redis.from_url(
                            os.getenv("REDIS_URL"), decode_responses=True
                        )
                    if rc:
                        # Example: decrease pending trades count if used by jobs
                        try:
                            if rc.exists("trade:pending_count"):
                                cur = rc.get("trade:pending_count")
                                if cur and cur.isdigit():
                                    newv = max(0, int(cur) - 1)
                                    rc.set("trade:pending_count", newv)
                        except Exception:
                            pass
                except Exception:
                    pass
                await update.message.reply_text(
                    f"Slip data for {symbol} has been cleaned up."
                )
            else:
                await update.message.reply_text(
                    f"No slip data found for {symbol} to clean up."
                )
        except Exception as e:
            logger.error(f"Slip cleanup failed: {e}")
            await update.message.reply_text("Trade closed, but slip cleanup failed.")
    else:
        await update.message.reply_text("Failed to close the trade.")


async def wallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the user's current spot wallet balances on Binance."""
    user_id = update.effective_user.id
    mode, _ = db.get_user_trading_mode_and_balance(user_id)
    is_admin = user_id == getattr(config, "ADMIN_USER_ID", None)

    if mode != "LIVE" and not is_admin:
        await update.message.reply_text(
            "This command is for LIVE mode only. Your paper wallet is managed separately via /balance."
        )
        return

    await update.message.reply_text(
        "Retrieving your spot wallet balances from Binance... 🏦"
    )

    try:
        # Admin/creator/father bypasses API key check
        admin_id = getattr(config, "ADMIN_USER_ID", None)
        if is_admin:
            balances = trade.get_all_spot_balances(admin_id)
        else:
            balances = trade.get_all_spot_balances(user_id)
        if balances is None:
            if is_admin:
                await update.message.reply_text(
                    "Admin wallet retrieval failed. Please check Binance connectivity.",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(
                    "Could not retrieve balances. Please ensure your API keys are set correctly with `/setapi`.",
                    parse_mode="Markdown",
                )
            return
        if not balances:
            await update.message.reply_text("Your spot wallet appears to be empty.")
            return

        # Fetch all prices at once for valuation
        all_tickers = trade.client.get_all_tickers()
        prices = {item["symbol"]: float(item["price"]) for item in all_tickers}

        valued_assets = []
        total_usdt_value = 0.0

        for balance in balances:
            asset = balance["asset"]
            total_balance = float(balance["free"]) + float(balance["locked"])

            if asset.upper() in ["USDT", "BUSD", "USDC", "FDUSD", "TUSD"]:
                usdt_value = total_balance
            else:
                pair = f"{asset}USDT"
                price = prices.get(pair)
                usdt_value = (total_balance * price) if price else 0

            if usdt_value > 1.0:  # Only show assets worth more than $1
                valued_assets.append(
                    {"asset": asset, "balance": total_balance, "usdt_value": usdt_value}
                )
                if asset.upper() not in ["USDT", "BUSD", "USDC", "FDUSD", "TUSD"]:
                    total_usdt_value += usdt_value

        # Add USDT itself to the total value at the end
        total_usdt_value += next(
            (b["usdt_value"] for b in valued_assets if b["asset"] == "USDT"), 0
        )

        # Sort by USDT value, descending
        valued_assets.sort(key=lambda x: x["usdt_value"], reverse=True)

        message = "💎 **Your Spot Wallet Holdings:**\n\n"
        for asset_info in valued_assets:
            balance_str = f"{asset_info['balance']:,.8f}".rstrip("0").rstrip(".")
            message += f"  - **{asset_info['asset']}**: `{balance_str}` (~${asset_info['usdt_value']:,.2f})\n"

        message += f"\n*Estimated Total Value:* `${total_usdt_value:,.2f}` USDT"

        await update.message.reply_text(message, parse_mode="Markdown")

    except trade.TradeError as e:
        await update.message.reply_text(
            f"⚠️ **Error!**\n\n*Reason:* `{e}`", parse_mode="Markdown"
        )





async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Places a live buy order. Premium feature.
    Usage: /buy <SYMBOL> <USDT_AMOUNT>
    """
    # Runtime guard: block live trades if Binance client unavailable
    try:
        import trade as _trade

        if not getattr(_trade, "BINANCE_AVAILABLE", False):
            await update.message.reply_text(
                "Live trading is currently disabled because the Binance client is unavailable. Please check /binance_status or contact the admin."
            )
            return
    except Exception:
        await update.message.reply_text(
            "Live trading is currently unavailable. Please try again later."
        )
        return

    user_id = update.effective_user.id
    mode, _ = db.get_user_trading_mode_and_balance(user_id)

    is_admin = user_id == getattr(config, "ADMIN_USER_ID", None)
    if mode != "LIVE" and not is_admin:
        await update.message.reply_text("Upgrade to Premium to use this feature.")
        return

    try:
        symbol = context.args[0].upper()
        usdt_amount = float(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text(
            "Please specify a symbol and amount.\nUsage: `/buy PEPEUSDT 11`",
            parse_mode="Markdown",
        )
        return

    if db.is_trade_open(user_id, symbol):
        await update.message.reply_text(
            f"You already have an open quest for {symbol}. Use /status to see it."
        )
        return

    await update.message.reply_text(
        f"Preparing to embark on a **LIVE** quest for **{symbol}** with **${usdt_amount:.2f}**...",
        parse_mode="Markdown",
    )

    # The /buy command is intentionally disabled in this deployment.
    # Live order placement is complex and can block or cause accidental trades from the bot runtime.
    await update.message.reply_text(
        "The /buy command is disabled. Use /autotrade (admin) or contact the bot administrator to perform live trades.",
        parse_mode="Markdown",
    )


async def checked_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows which symbols the AI has checked recently."""
    user_id = update.effective_user.id

    if user_id != getattr(config, "ADMIN_USER_ID", None):
        await update.message.reply_text("This is an admin-only command.")
        return

    checked_symbols_log = context.bot_data.get("checked_symbols", [])
    if not checked_symbols_log:
        await update.message.reply_text("The AI has not checked any symbols yet.")
        return

    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)

    # Filter for the last hour and get unique symbols
    recent_checks = sorted(
        list({symbol for ts, symbol in checked_symbols_log if ts > one_hour_ago})
    )

    # Cleanup old entries from the log to prevent it from growing indefinitely
    two_hours_ago = datetime.now(timezone.utc) - timedelta(hours=2)
    context.bot_data["checked_symbols"] = [
        (ts, symbol) for ts, symbol in checked_symbols_log if ts > two_hours_ago
    ]

    if not recent_checks:
        await update.message.reply_text(
            "The AI has not checked any symbols in the last hour."
        )
        return

    message = "📈 **AI Oracle's Recent Scans (Last Hour):**\n\n" + ", ".join(
        f"`{s}`" for s in recent_checks
    )
    await update.message.reply_text(message, parse_mode="Markdown")


async def review_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reviews the user's completed trade performance."""
    user_id = update.effective_user.id
    closed_trades = db.get_closed_trades(user_id)

    if not closed_trades:
        await update.message.reply_text(
            "You have no completed trades to review. Close a trade using `/close <id>`.",
            parse_mode="Markdown",
        )
        return

    wins = 0
    losses = 0
    total_profit_percent = 0.0
    best_trade = None
    worst_trade = None
    # Use -inf and inf to correctly handle all possible P/L values
    best_pnl = -float("inf")
    worst_pnl = float("inf")

    for t in closed_trades:
        profit_percent = ((t["sell_price"] - t["buy_price"]) / t["buy_price"]) * 100

        # Track best and worst trades
        if profit_percent > best_pnl:
            best_pnl = profit_percent
            best_trade = t
        if profit_percent < worst_pnl:
            worst_pnl = profit_percent
            worst_trade = t

        if profit_percent >= 0:
            wins += 1
        else:
            losses += 1
        total_profit_percent += profit_percent

    total_trades = len(closed_trades)
    win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
    avg_pnl_percent = total_profit_percent / total_trades if total_trades > 0 else 0

    message = f"""🌟 **LunessaSignals Performance Review** 🌟

**Completed Quests:** {total_trades}
**Victories (Wins):** {wins}
**Setbacks (Losses):** {losses}
**Win Rate:** {win_rate:.2f}%

**Average P/L:** `{avg_pnl_percent:,.2f}%`
"""

    if best_trade and worst_trade:
        message += (
            f"\n"
            f"**Top Performers:**\n"
            f"🚀 **Best Quest:** {best_trade['coin_symbol']} (`{best_pnl:+.2f}%`)\n"
            f"💔 **Worst Quest:** {worst_trade['coin_symbol']} (`{worst_pnl:+.2f}%`)\n"
        )

    message += "\nKeep honing your skills, seeker. The market's rhythm is complex."

    # Send the message and also produce a detailed report via reporter
    try:
        await update.message.reply_text(message, parse_mode="Markdown")
    except Exception:
        # Fallback to sending plain text
        await update.message.reply_text(message)

    # Prepare review entries and send a detailed report via reporter.format_performance_report
    reviews = []
    for t in closed_trades:
        reviews.append(
            {
                "timestamp": t.get("closed_at") or t.get("sell_ts") or int(time.time()),
                "rating": round(
                    ((t["sell_price"] - t["buy_price"]) / t["buy_price"]) * 100, 2
                ),
                "review_text": f"Trade {t.get('id')} {t.get('coin_symbol')} P/L: {((t['sell_price'] - t['buy_price'])/t['buy_price'])*100:.2f}%",
            }
        )

    try:
        from reporter import format_performance_report

        report_text = format_performance_report(user_id, reviews)
        # Send as a single message (Markdown formatted)
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=report_text,
                parse_mode="Markdown",
            )
        except Exception:
            # Fallback to plain send without parse mode
            await context.bot.send_message(
                chat_id=update.effective_chat.id, text=report_text
            )
    except Exception:
        # If reporter import or send fails, log the report and print
        logger.exception("Failed to prepare/send performance report; logging instead.")
        print("\n".join([f"{r['timestamp']}: {r['review_text']}" for r in reviews]))


async def review_export_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Admin-only: export a user's performance reviews as a CSV attachment."""
    user_id = update.effective_user.id
    if user_id != getattr(config, "ADMIN_USER_ID", None):
        await update.message.reply_text("This is an admin-only command.")
        return

    target_user = str(user_id)
    if context.args:
        target_user = context.args[0]

    try:
        import performance_reviews

        reviews = performance_reviews.get_reviews(str(target_user))
    except Exception as e:
        logger.exception("Failed to load reviews for export: %s", e)
        await update.message.reply_text("Failed to load reviews for export.")
        return

    # Build CSV in memory
    csv_io = io.StringIO()
    writer = csv.writer(csv_io)
    writer.writerow(["id", "timestamp", "iso_timestamp", "rating", "review_text"])
    for r in reviews:
        ts = int(r.get("timestamp", 0) or 0)
        writer.writerow(
            [
                r.get("id"),
                ts,
                datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                r.get("rating"),
                r.get("review_text"),
            ]
        )

    csv_bytes = csv_io.getvalue().encode("utf-8")
    csv_io.close()

    bio = io.BytesIO(csv_bytes)
    bio.name = (
        f"reviews_{target_user}_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.csv"
    )

    try:
        await context.bot.send_document(chat_id=update.effective_chat.id, document=bio)
    except Exception:
        # Fallback: send plain text preview
        await update.message.reply_text(
            "Unable to send CSV document; here is a preview:\n"
            + csv_bytes.decode("utf-8")[:4000]
        )


async def top_trades_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Displays the user's top 3 most profitable closed trades."""
    user_id = update.effective_user.id
    top_trades = db.get_top_closed_trades(user_id, limit=3)

    if not top_trades:
        await update.message.reply_text(
            "You have no completed profitable quests to rank. Close a winning trade to enter the Hall of Fame!",
            parse_mode="Markdown",
        )
        return

    message = (
        "🏆 **Your Hall of Fame** 🏆\n\n_Here are your most legendary victories:_\n\n"
    )
    rank_emojis = ["🥇", "🥈", "🥉"]

    for i, trade_entry in enumerate(top_trades):
        emoji = rank_emojis[i] if i < len(rank_emojis) else "🔹"
        message += f"{emoji} **{trade_entry['coin_symbol']}**: `{trade_entry['pnl_percent']:+.2f}%`\n"

    message += "\nMay your future quests be even more glorious!"
    await update.message.reply_text(message, parse_mode="Markdown")


async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the bot owner's referral link and information."""
    if not config.ADMIN_REFERRAL_CODE:
        await update.message.reply_text(
            "The referral program is not configured for this bot."
        )
        return

    referral_link = f"https://www.binance.com/en/activity/referral-entry/CPA?ref={config.ADMIN_REFERRAL_CODE}"

    message = f"""🤝 **Invite Friends, Earn Together!** 🤝

Refer friends to buy crypto on Binance, and we both get rewarded!

**The Deal:**
When your friend signs up using the link below and buys over $50 worth of crypto, you both receive a **$100 trading fee rebate voucher**.

**Your Tools to Share:**

🔗 **Referral Link:**
`{referral_link}`

🏷️ **Referral Code:**
`{config.ADMIN_REFERRAL_CODE}`

Share the link or code with your friends to start earning. Thank you for supporting the LunessaSignals project!"""
    await update.message.reply_text(
        message, parse_mode="Markdown", disable_web_page_preview=True
    )


async def leaderboard_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Displays the global leaderboard of top trades."""
    top_trades = db.get_global_top_trades(limit=3)

    if not top_trades:
        await update.message.reply_text(
            "The Hall of Legends is still empty. No legendary quests have been completed yet!",
            parse_mode="Markdown",
        )
        return

    message = "🏆 **Hall of Legends: Global Top Quests** 🏆\n\n_These are the most glorious victories across the realm:_\n\n"
    rank_emojis = ["🥇", "🥈", "🥉"]

    for i, trade_entry in enumerate(top_trades):
        emoji = rank_emojis[i] if i < len(rank_emojis) else "🔹"
        user_id = trade_entry["user_id"]
        user_name = "A mysterious adventurer"  # Default name
        try:
            chat = await context.bot.get_chat(user_id)
            user_name = chat.first_name
        except Exception as e:
            logger.warning(
                f"Could not fetch user name for {user_id} for leaderboard: {e}"
            )

        message += f"{emoji} **{trade['coin_symbol']}**: `{trade['pnl_percent']:+.2f}%` (by {user_name})\n"

    message += "\nWill your name be etched into legend?"
    await update.message.reply_text(message, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays a help message with all available commands."""
    help_text = """
<b>LunessaSignals's Guide 🔮</b>

Here are the commands to guide your journey:

<b>--- Account & Setup ---</b>
<b>/start</b> - Begin your journey
<b>/setapi</b> <code>KEY SECRET</code> - Link your Binance keys (in private chat)
<b>/linkbinance</b> - Instructions for creating API keys
<b>/wallet</b> - View your full Binance Spot Wallet
<b>/myprofile</b> - View your profile and settings
<b>/settings</b> - [Premium] Customize your trading parameters
<b>/subscribe</b> - See premium benefits and how to upgrade

<b>--- Trading & Analysis ---</b>
<b>/quest</b> <code>SYMBOL</code> - Scan a crypto pair for opportunities
<b>/status</b> - View your open trades and watchlist
<b>/balance</b> - Check your LIVE or PAPER balance
<b>/close</b> <code>ID</code> - Manually complete a quest (trade)
<b>/import</b> <code>SYMBOL [PRICE]</code> - Log an existing trade
<b>/papertrade</b> - Toggle practice mode

<b>--- Performance & Community ---</b>
<b>/review</b> - See your personal performance stats
<b>/top_trades</b> - View your 3 best trades
<b>/referral</b> - Get your referral link to invite friends
<b>/autotrade</b> - [Admin] Enable or disable automatic trading. <i>When enabled, the bot will scan for strong buy signals and execute trades for you. You will be notified of all actions. Use <code>/autotrade on</code> or <code>/autotrade off</code> to control.</i>
<b>/leaderboard</b> - See the global top 3 trades

<b>--- General ---</b>
<b>/ask</b> <code>QUESTION</code> - Ask the AI Oracle about trading
<b>/learn</b> - Get quick educational tips
<b>/pay</b> - See how to support LunessaSignals's development
<b>/safety</b> - Read important trading advice
<b>/resonate</b> - A word of wisdom from LunessaSignals
<b>/help</b> - Show this help message
"""
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)


async def myprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the user's profile information, including tier and settings."""
    user_id = update.effective_user.id
    user_tier = db.get_user_tier_db(user_id)

    # Canonical source: database-effective settings which layer DB custom
    # columns over subscription-tier defaults. Prefer autotrade_settings
    # (written by the /settings handler) for stored per-user overrides.
    settings = db.get_user_effective_settings(user_id)

    # Merge stored autotrade settings (if any) which use the `autotrade:settings:{user_id}` key.
    # Merge autotrade_settings stored overrides. Use importlib to prefer the
    # package-qualified module (`src.autotrade_settings`) when available so
    # tests that import via `src` still resolve the same module.
    try:
        import importlib

        _mod = None
        try:
            _mod = importlib.import_module("src.autotrade_settings")
        except Exception:
            try:
                _mod = importlib.import_module("autotrade_settings")
            except Exception:
                _mod = None

        if _mod and hasattr(_mod, "get_user_settings"):
            stored_overrides = _mod.get_user_settings(user_id) or {}
            for k, v in (
                stored_overrides.items() if isinstance(stored_overrides, dict) else []
            ):
                try:
                    upper_k = k.upper()
                    if upper_k in settings:
                        settings[upper_k] = v
                    else:
                        settings[k] = v
                except Exception:
                    continue
    except Exception as e:
        logger.debug(f"Could not merge autotrade_settings for user {user_id}: {e}")

    # Backward-compatibility: if a legacy key `user:{id}:settings` exists in Redis,
    # merge it (but do not let it override canonical DB/autotrade values silently).
    try:
        # Attempt to read a legacy Redis key. Use a sane default REDIS URL so
        # tests that monkeypatch `redis.from_url` will receive the fake client.
        redis_url = (
            os.getenv("REDIS_URL")
            or getattr(config, "REDIS_URL", None)
            or "redis://localhost:6379/0"
        )
        rc = redis.from_url(redis_url, decode_responses=True)
        legacy_key = f"user:{user_id}:settings"
        if rc.exists(legacy_key):
            try:
                stored = rc.get(legacy_key)
                parsed = json.loads(stored) if stored else {}
                for k, v in parsed.items():
                    upper_k = k.upper()
                    if upper_k in settings:
                        settings[upper_k] = v
                    else:
                        settings[k] = v
            except Exception:
                logger.debug(
                    f"Could not parse legacy Redis settings for user {user_id}"
                )
    except Exception as e:
        logger.debug(
            f"Redis unavailable when loading legacy profile for {user_id}: {e}"
        )
    trading_mode, paper_balance = db.get_user_trading_mode_and_balance(user_id)

    username = update.effective_user.username or "(not set)"
    autotrade = "Enabled" if db.get_autotrade_status(user_id) else "Disabled"
    message = f"""*Your Profile*

*User ID:* `{user_id}`
*Username:* @{username}
*Tier:* {user_tier}
*Trading Mode:* {trading_mode}
*Autotrade:* {autotrade}"""
    if trading_mode == "LIVE":
        # Optionally, fetch and show real USDT balance here
        message += "\n*USDT Balance:* (see /wallet)"
    else:
        message += f"\n*Paper Balance:* `${paper_balance:,.2f}`"
    message += "\n\n*Custom Settings:*"
    message += f"\n- RSI Buy: {settings.get('RSI_BUY_THRESHOLD', 'N/A')}"
    message += f"\n- RSI Sell: {settings.get('RSI_SELL_THRESHOLD', 'N/A')}"
    # Historically some deployments used PROFIT_TARGET_PERCENTAGE as the displayed stop-loss.
    stop_loss_display = settings.get("STOP_LOSS_PERCENTAGE")
    if stop_loss_display is None:
        stop_loss_display = settings.get("PROFIT_TARGET_PERCENTAGE", "N/A")
    message += f"\n- Stop Loss: {stop_loss_display}%"
    # Trailing activation: prefer explicit setting, fallback to a sensible default
    trailing_activation = settings.get("TRAILING_PROFIT_ACTIVATION_PERCENT")
    if trailing_activation is None:
        trailing_activation = 1.5
    message += "\n- Trailing Activation: %s%%" % trailing_activation
    message += (
        f"\n- Trailing Drop: {settings.get('TRAILING_STOP_DROP_PERCENT', 'N/A')}%"
    )
    if user_tier == "PREMIUM":
        message += (
            f"\n- Bollinger Band Width: {settings.get('BOLLINGER_BAND_WIDTH', 2.0)}"
        )
        message += (
            f"\n- MACD Signal Threshold: {settings.get('MACD_SIGNAL_THRESHOLD', 0)}"
        )
    await update.message.reply_text(message, parse_mode="Markdown")


async def view_settings_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Allows Premium users to view and customize their trading settings."""
    user_id = update.effective_user.id
    user_tier = db.get_user_tier_db(user_id)

    def escape_markdown(text: str) -> str:
        # Use the library helper where available for correct MarkdownV2 escaping.
        try:
            from telegram.helpers import escape_markdown as _escape

            return _escape(text, version=2)
        except Exception:
            # Fallback: minimal escaping for common MarkdownV2 metacharacters
            # Escape common characters conservatively
            replacements = {
                "\\": "\\\\",
                "_": "\\_",
                "*": "\\*",
                "[": "\\[",
                "]": "\\]",
                "(": "\\(",
                ")": "\\)",
                "~": "\\~",
                "`": "\\`",
                ">": "\\>",
                "#": "\\#",
                "+": "\\+",
                "-": "\\-",
                "=": "\\=",
                "|": "\\|",
                "{": "\\{",
                "}": "\\}",
                ".": "\\.",
                "!": "\\!",
            }
            out = text
            for k, v in replacements.items():
                out = out.replace(k, v)
            return out

    if user_tier != "PREMIUM":
        await update.message.reply_text("Upgrade to Premium to use this feature.")
        return

    # If no args, show current settings and usage
    if not context.args:
        settings = db.get_user_effective_settings(user_id)
        message = f"""⚙️ **Your Custom Trading Settings** ⚙️

| Setting                   | Value      |
|---------------------------|------------|
| RSI Buy Threshold         | `{settings['RSI_BUY_THRESHOLD']}`
| RSI Sell Threshold        | `{settings['RSI_SELL_THRESHOLD']}`
| Stop Loss (%)             | `{settings['STOP_LOSS_PERCENTAGE']}`
| Trailing Activation (%)  | `{settings['TRAILING_PROFIT_ACTIVATION_PERCENT']}`
| Trailing Drop (%)         | `{settings['TRAILING_STOP_DROP_PERCENT']}`
| Bollinger Band Width      | `{settings.get('BOLLINGER_BAND_WIDTH', 2.0)}`
| MACD Signal Threshold     | `{settings.get('MACD_SIGNAL_THRESHOLD', 0)}`
| Trade Size (USDT)         | `{settings.get('TRADE_SIZE_USDT', 5.0)}`

---
**Change a setting:**
  `/settings <name> <value>`
  Example: `/settings rsi_buy 40`

**Set trade size:**
  `/settings trade_size 10`
  (Minimum: $5.00)

**Reset a setting to default:**
  `/settings <name> reset`

**Available settings:** rsi_buy, rsi_sell, stop_loss, trailing_activation, trailing_drop, trade_size, bollinger_band_width, macd_signal_threshold"""

        await update.message.reply_text(
            escape_markdown(message), parse_mode="MarkdownV2"
        )
        return

    # Logic to set a value
    try:
        setting_name = context.args[0].lower()
        value_str = context.args[1].lower()
    except IndexError:
        await update.message.reply_text(
            escape_markdown("Invalid format. Usage: `/settings <name> <value>`"),
            parse_mode="MarkdownV2",
        )
        return

    try:
        valid_settings = list(db.SETTING_TO_COLUMN_MAP.keys()) + ["trade_size"]
        if setting_name not in valid_settings:
            await update.message.reply_text(
                escape_markdown(
                    f"Unknown setting '{setting_name}'. Valid settings are: {', '.join(valid_settings)}"
                ),
                parse_mode="MarkdownV2",
            )
            return

        if setting_name == "trade_size":
            if value_str == "reset":
                db.update_user_setting(user_id, "trade_size", 5.0)
                await update.message.reply_text(
                    escape_markdown("Trade size reset to $5.00 (minimum)."),
                    parse_mode="MarkdownV2",
                )
                return
            try:
                new_value = float(value_str)
            except ValueError:
                await update.message.reply_text(
                    escape_markdown(
                        f"Invalid value '{value_str}'. Please provide a number (e.g., 8.5) or 'reset'."
                    ),
                    parse_mode="MarkdownV2",
                )
                return
            if new_value < 5.0:
                await update.message.reply_text(
                    escape_markdown("Trade size must be at least $5.00."),
                    parse_mode="MarkdownV2",
                )
                return
            db.update_user_setting(user_id, "trade_size", new_value)
            await update.message.reply_text(
                escape_markdown(
                    f"✅ Successfully updated trade size to **${new_value:.2f}**."
                ),
                parse_mode="MarkdownV2",
            )
            return

        # Existing settings logic
        new_value = None if value_str == "reset" else float(value_str)
        if new_value is not None and new_value <= 0:
            await update.message.reply_text(
                escape_markdown("Value must be a positive number."),
                parse_mode="MarkdownV2",
            )
            return
        db.update_user_setting(user_id, setting_name, new_value)
        await update.message.reply_text(
            escape_markdown(
                f"✅ Successfully updated **{setting_name}** to **{value_str}**."
            ),
            parse_mode="MarkdownV2",
        )
    except ValueError:
        await update.message.reply_text(
            escape_markdown(
                f"Invalid value '{value_str}'. Please provide a number (e.g., 8.5) or 'reset'."
            ),
            parse_mode="MarkdownV2",
        )


async def autotrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to control the AI autotrading feature."""
    user_id = update.effective_user.id
    if user_id != getattr(config, "ADMIN_USER_ID", None):
        await update.message.reply_text("This is an admin-only command.")
        return

    if not context.args:
        status = "ENABLED" if db.get_autotrade_status(user_id) else "DISABLED"
        coins = getattr(config, "AI_MONITOR_COINS", [])
        coins_str = ", ".join(coins) if coins else "None"
        await update.message.reply_text(
            f"""🤖 **AI Autotrade Status:** `{status}`

<b>Monitored Coins:</b> {coins_str}
<b>What is Autotrade?</b>
When enabled, the bot will automatically scan for strong buy signals and execute trades for you. You will be notified of all actions.
Use <code>/autotrade on</code> to enable, or <code>/autotrade off</code> to disable.""",
            parse_mode=ParseMode.HTML,
        )
        return

    sub_command = context.args[0].lower()
    if sub_command == "on":
        db.set_autotrade_status(user_id, True)
        await update.message.reply_text(
            """🤖 <b>AI Autotrade has been ENABLED.</b>

The bot will now scan for strong buy signals and execute trades for you automatically. You will receive notifications for every action taken.

To disable, use <code>/autotrade off</code>.""",
            parse_mode=ParseMode.HTML,
        )
    elif sub_command == "off":
        db.set_autotrade_status(user_id, False)
        await update.message.reply_text(
            """🤖 <b>AI Autotrade has been DISABLED.</b>

The bot will no longer execute trades automatically. You are now in manual mode.

To enable again, use <code>/autotrade on</code>.""",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            "Invalid command. Use <code>/autotrade on</code> or <code>/autotrade off</code>.",
            parse_mode=ParseMode.HTML,
        )


async def addcoins_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Premium command to add or reset coins for AI monitoring."""
    user_id = update.effective_user.id
    if (
        user_id != getattr(config, "ADMIN_USER_ID", None)
        and db.get_user_tier_db(user_id) != "PREMIUM"
    ):
        await update.message.reply_text("Upgrade to Premium to use this feature.")
        return

    args = context.args
    if not args:
        coins = getattr(config, "AI_MONITOR_COINS", [])
        coins_str = ", ".join(coins) if coins else "None"
        await update.message.reply_text(
            f"Current monitored coins: {coins_str}\nUsage: /addcoins OMbtc, ARBUSDT, ... or /addcoins reset",
            parse_mode="Markdown",
        )
        return

    if args[0].lower() == "reset":
        config.AI_MONITOR_COINS = [
            "BTCUSDT",
            "ETHUSDT",
            "BNBUSDT",
            "SOLUSDT",
            "ARBUSDT",
            "PEPEUSDT",
            "DOGEUSDT",
            "SHIBUSDT",
        ]
        await update.message.reply_text("AI_MONITOR_COINS has been reset to default.")
        return

    # Add coins (comma or space separated)
    coins_to_add = []
    for arg in args:
        coins_to_add += [
            c.strip().upper() for c in arg.replace(",", " ").split() if c.strip()
        ]
    # Remove duplicates, add to config
    current_coins = set(getattr(config, "AI_MONITOR_COINS", []))
    new_coins = current_coins.union(coins_to_add)
    config.AI_MONITOR_COINS = list(new_coins)
    coins_str = ", ".join(config.AI_MONITOR_COINS)
    await update.message.reply_text(f"Updated monitored coins: {coins_str}")


async def set_api_command_primary(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Securely store a user's Binance API key and secret for autotrade features.

    Usage: /setapi <KEY> <SECRET>  (send in a private chat)
    Only PREMIUM users (or the admin) may store API keys.
    """
    # Ensure this is done in a private chat to avoid leaking keys
    try:
        chat_type = update.effective_chat.type if update.effective_chat else None
    except Exception:
        chat_type = None
    if chat_type != "private":
        await update.message.reply_text(
            "For your safety, please send API keys in a private chat with the bot."
        )
        return

    user_id = update.effective_user.id
    # Allow admin to set keys regardless of tier
    user_tier = db.get_user_tier_db(user_id)
    if user_id != getattr(config, "ADMIN_USER_ID", None) and user_tier != "PREMIUM":
        await update.message.reply_text(
            "API key linking is a Premium feature. Please upgrade to Premium to use it."
        )
        return

    # Parse arguments (KEY SECRET)
    try:
        api_key = context.args[0].strip()
        secret_key = context.args[1].strip()
    except Exception:
        await update.message.reply_text(
            "Usage: /setapi <KEY> <SECRET> — send this in a private chat with the bot."
        )
        return

    # Ensure encryption key is present
    if not getattr(config, "BINANCE_ENCRYPTION_KEY", None):
        logger.error(
            "BINANCE_ENCRYPTION_KEY is not configured; cannot store API keys securely"
        )
        await update.message.reply_text(
            "Server misconfiguration: encryption key missing. Contact the administrator."
        )
        return

    try:
        # Use the db helper to encrypt and store keys
        db.store_user_api_keys(user_id, api_key, secret_key)
        await update.message.reply_text(
            "✅ Your API keys have been stored securely. Autotrade and live features are now available to you.",
            parse_mode=ParseMode.MARKDOWN,
        )
        # Notify admin (best-effort)
        try:
            from admin_alerts import send_admin_alert

            send_admin_alert(f"User <b>{user_id}</b> stored Binance API keys.")
        except Exception:
            logger.debug("Failed to send admin alert for setapi; continuing.")
    except Exception as e:
        logger.exception("Failed to store API keys for user %s: %s", user_id, e)
        await update.message.reply_text(
            "Failed to save API keys. Please contact the administrator."
        )


async def activate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-friendly activation shortcut.

    Usage (admin only): /activate <TELEGRAM_ID> [TIER] [MONTHS]
    Defaults: TIER=GOLD, MONTHS=1
    """
    user_id = update.effective_user.id
    if user_id != getattr(config, "ADMIN_USER_ID", None):
        await update.message.reply_text(
            "⛔ You are not authorized to perform this action."
        )
        return

    try:
        target_id = int(context.args[0])
    except Exception:
        await update.message.reply_text(
            "Usage: /activate <TELEGRAM_ID> [TIER] [MONTHS]"
        )
        return

    tier_name = context.args[1].upper() if len(context.args) > 1 else "GOLD"
    try:
        months = int(context.args[2]) if len(context.args) > 2 else 1
    except Exception:
        months = 1

    if tier_name not in config.SUBSCRIPTION_TIERS:
        await update.message.reply_text(
            f"Invalid tier: {tier_name}. Available: {', '.join(config.SUBSCRIPTION_TIERS.keys())}"
        )
        return

    expiry_date = datetime.now(timezone.utc) + timedelta(days=30 * months)
    expires_str = expiry_date.strftime("%Y-%m-%d %H:%M:%S")

    db.update_user_subscription(target_id, tier=tier_name, expires=expires_str)
    await update.message.reply_text(
        f"✅ Activated {target_id} as {tier_name} until {expires_str}"
    )
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                f"🎉 Your subscription has been activated to **{tier_name}**!\n"
                f"Expires: {expiry_date.strftime('%d %b %Y')}"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        # ignore notification errors
        pass


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Broadcast is a Premium feature.")


async def papertrade_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await update.message.reply_text("Paper trading is a Premium feature.")


async def verifypayment_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Verifies payment and upgrades user tier."""
    user_id = update.effective_user.id
    if user_id != getattr(config, "ADMIN_USER_ID", None):
        await update.message.reply_text(
            "This command can only be used by the bot administrator."
        )
        return

    try:
        target_telegram_id = int(context.args[0])
        payment_reference = context.args[1]
        tier_name = context.args[2].upper()  # e.g., BASIC, PRO, ELITE
        duration_months = int(context.args[3])  # e.g., 1, 3, 12
    except (IndexError, ValueError):
        await update.message.reply_text(
            "Usage: `/verifypayment <TELEGRAM_ID> <PAYMENT_REFERENCE> <TIER> <DURATION_MONTHS>`\nExample: `/verifypayment 123456789 BKASH_TRX12345 PRO 1`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Validate tier
    if tier_name not in config.SUBSCRIPTION_TIERS:
        await update.message.reply_text(
            f"Invalid tier: {tier_name}. Available tiers: {', '.join(config.SUBSCRIPTION_TIERS.keys())}"
        )
        return

    # Calculate expiry date
    expiry_date = datetime.now(timezone.utc) + timedelta(days=30 * duration_months)

    # Update user tier in DB
    db.update_user_subscription(
        target_telegram_id,
        tier=tier_name,
        expires=expiry_date.strftime("%Y-%m-%d %H:%M:%S"),
    )

    await update.message.reply_text(
        f"""✅ Payment verified for user `{target_telegram_id}` (Ref: `{payment_reference}`).\nTier upgraded to **{tier_name}** until `{expiry_date.strftime('%Y-%m-%d %H:%M:%S UTC')}`.""",
        parse_mode=ParseMode.MARKDOWN,
    )

    # Notify the user whose tier was updated
    try:
        await context.bot.send_message(
            chat_id=target_telegram_id,
            text=f"""🎉 Your LunessaSignals subscription has been upgraded to **{tier_name}**!\nIt is valid until `{expiry_date.strftime('%Y-%m-%d %H:%M:%S UTC')}`.\nThank you for your support!""",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.warning(
            f"Could not send notification to user {target_telegram_id} about tier upgrade: {e}"
        )


async def confirm_payment_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Admin command to confirm payment and activate a standard subscription."""
    if update.effective_user.id != getattr(config, "ADMIN_USER_ID", None):
        await update.message.reply_text(
            "⛔ You are not authorized to perform this action."
        )
        return

    try:
        target_user_id = int(context.args[0])
        # Default to 1 month of "GOLD" tier
        tier_name = "GOLD"
        duration_months = 1
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: `/confirm_payment <USER_ID>`")
        return

    # Calculate expiry date
    expiry_date = datetime.now(timezone.utc) + timedelta(days=30 * duration_months)
    expires_str = expiry_date.strftime("%Y-%m-%d %H:%M:%S")

    # Update user in DB
    db.update_user_subscription(target_user_id, tier=tier_name, expires=expires_str)

    await update.message.reply_text(
        f"✅ Subscription activated for user `{target_user_id}`.\n"  # Corrected: Removed unnecessary escape for newline
        f"Tier: **{tier_name}**\n"
        f"Expires: **{expires_str}**"
    )

    # Notify the user
    try:
        await context.bot.send_message(
            chat_id=target_user_id,
            text=(
                f"🎉 Your subscription has been activated!\n\n"
                f"You are now a **{tier_name}** member.\n"
                f"Your access expires on {expiry_date.strftime('%d %b %Y')}."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.warning(
            f"Could not send subscription activation notification to user {target_user_id}: {e}"
        )


async def pay_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays payment information and instructions."""
    bank_info = """
🏦 *Bank Transfer Instructions*

*Account Name*: Shamim Reza Saikat
*Account Number*: 1534105036454001
*Bank Name*: BTAC Bank Ltd.
*Branch*: Badda
*SWIFT Code*: BRAKBDDH

📸 After sending the payment, please take a screenshot and send it via WhatsApp to *01717948095* for manual confirmation.
"""
    await update.message.reply(bank_info, parse_mode=ParseMode.MARKDOWN)
    await update.message.reply(
        "✅ Once verified, your subscription will be activated and you'll receive a confirmation message via Telegram."
    )


async def usercount_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("User count is a Premium feature.")


# ---
# Restore previous /ask command logic ---
async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /ask command using Gemini AI for Premium users."""
    user_id = update.effective_user.id
    user_tier = db.get_user_tier_db(user_id)
    if user_tier != "PREMIUM":
        await update.message.reply_text("Upgrade to Premium to use the AI Oracle.")
        return
    question = " ".join(context.args) if context.args else None
    if not question:
        await update.message.reply_text(
            "Please provide a question. Usage: /ask Should I buy ARBUSDT now?"
        )
        return
    await update.message.reply_text("Consulting the AI Oracle... Please wait.")
    try:
        from autotrade_jobs import get_ai_suggestions

        answer = await get_ai_suggestions(question)
        if answer:
            await update.message.reply_text(f"🔮 AI Oracle says:\n\n{answer}")
        else:
            await update.message.reply_text(
                "The AI Oracle could not answer at this time."
            )
    except Exception as e:
        logger.error(f"AI Oracle error: {e}")
        await update.message.reply_text("The AI Oracle could not answer at this time.")


# ---
# Placeholder Command Handlers ---
async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays subscription tiers and benefits, or the user's current status."""
    user_id = update.effective_user.id
    tier, expires_str = db.get_user_subscription_db(user_id)

    if tier != "FREE" and expires_str:
        try:
            # Using fromisoformat for robust parsing
            expires_dt = datetime.fromisoformat(expires_str).astimezone(timezone.utc)
            now_utc = datetime.now(timezone.utc)

            # Check if expired
            if expires_dt < now_utc:
                # This is a good place to also downgrade the user in the DB
                db.update_user_subscription(user_id, tier="FREE", expires=None)
                message = (
                    "⚠️ Your subscription has expired. You are now on the FREE tier.\n\n"
                    "Use the /pay command to renew your subscription and regain access to premium features."
                )
                await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
                return

            days_remaining = (expires_dt - now_utc).days
            expiry_date_formatted = expires_dt.strftime("%d %b %Y")

            message = (
                f"🎉 **You are already a {tier.capitalize()} member!**\n\n"
                f"Your subscription expires on **{expiry_date_formatted}**.\n"
                f"Days Remaining: **{days_remaining}**\n\n"
                "Thank you for your support!"
            )
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
            return
        except (ValueError, TypeError):
            # Fallback if date parsing fails for some reason
            pass  # Will proceed to show generic upgrade message

    # For FREE users or if something went wrong with date parsing
    subscribe_info = """
🌟 **LunessaSignals Subscription Tiers** 🌟

Unlock the full power of LunessaSignals with our premium tiers!

| Tier        | Features Included                              | Duration     |
|-------------|--------------------------------------------------|--------------|
| Free        | Basic signals, manual trading only              | Unlimited    |
| Gold        | Premium signals, auto-trade access, priority    | 30 days      |
| Platinum    | All Gold features + early access to new tools   | 90 days      |

---

To upgrade, use the `/pay` command to see payment methods. After payment, the admin will verify it and activate your new tier.

**How to activate Premium:**
1.  Use the `/pay` command to get payment instructions.
2.  After making the payment, contact the admin with your payment details.
3.  The admin will verify the payment and activate your premium subscription.

**Binance API IP Whitelisting:**
For live trading, you need to whitelist our server IPs in your Binance API settings.
Please add the following IPs to your Binance API key configuration:
`13.228.225.19`
`18.142.128.26`
`54.254.162.138`
"""
    await update.message.reply_text(subscribe_info, parse_mode=ParseMode.MARKDOWN)


async def linkbinance_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await update.message.reply_text(
        "This is a placeholder for the linkbinance command."
    )


async def learn_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("This is a placeholder for the learn command.")


async def activate_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.warning("activate_user_command is not yet implemented.")


async def set_api_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Duplicate stub removed; the real implementation lives earlier in this file.
    # This alias delegates to the primary implementation defined above.
    return await set_api_command_primary(update, context)


async def slip_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles incoming trade slips sent as text messages.
    """
    logger.info("Slip message received.")
    if not update.message or not update.message.text:
        return

    slip_text = update.message.text
    user_id = update.effective_user.id

    try:
        # 1. Parse the slip
        slip_data = parse_slip(slip_text)
        # Avoid logging raw slip_data which may contain API keys or secrets
        try:
            from .logging_utils import mask_secrets

            safe_slip = mask_secrets(slip_data)
        except Exception:
            safe_slip = "[masked slip data]"
        logger.info(f"Successfully parsed slip for user {user_id}: {safe_slip}")
        slip_data["user_id"] = user_id

        # 2. Validate the trade
        is_valid, reason = redis_validator.validate_trade(slip_data)
        if not is_valid:
            await update.message.reply_text(f"❌ Trade validation failed: {reason}")
            return

        # 3. Execute the trade (placeholder)
        execution_result = trade_executor.execute_trade(slip_data)

        # 4. Log and Notify
        await update.message.reply_text(execution_result)

    except SlipParseError as e:
        logger.error(f"Failed to parse slip for user {user_id}. Error: {e}")
        await update.message.reply_text(f"⚠️ Invalid slip format: {e}")
    except Exception as e:
        logger.error(
            f"An unexpected error occurred handling slip for user {user_id}: {e}",
            exc_info=True,
        )
        await update.message.reply_text(
            "An unexpected server error occurred while processing your slip."
        )


async def post_init(application: Application) -> None:
    """Runs once after the bot is initialized."""
    logger.info("Running post-initialization setup...")

    # Ensure no webhook is set before starting polling
    try:
        logger.info("Attempting to delete any pre-existing webhook...")
        await application.bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook deleted successfully.")
    except Exception as e:
        logger.warning(f"Could not delete webhook during post_init: {e}")

    # Start the Redis pub/sub listener in the background
    try:
        logger.info("Starting Redis pub/sub listener for real-time toggles...")
        asyncio.create_task(_redis_pubsub_listener(application))
    except Exception as e:
        logger.error(f"Failed to start Redis pub/sub listener: {e}")

    # Notify admin if Binance is unavailable at startup
    try:
        import trade as _trade

        if not getattr(_trade, "BINANCE_AVAILABLE", False):
            admin_id = getattr(config, "ADMIN_USER_ID", None)
            if admin_id:
                msg = "⚠️ **STARTUP WARNING** ⚠️\nBinance client is not available. Live trading features are disabled."
                if err := getattr(_trade, "BINANCE_INIT_ERROR", None):
                    msg += f"\n`Reason: {err}`"
                await application.bot.send_message(
                    chat_id=admin_id, text=msg, parse_mode=ParseMode.MARKDOWN
                )
    except Exception as e:
        logger.warning(f"Failed to send Binance status alert to admin: {e}")


async def _global_error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    """Log Errors caused by Updates."""
    err = getattr(context, "error", None)
    if isinstance(err, TelegramConflict):
        logger.warning(
            "Telegram Conflict detected: another getUpdates/webhook may be active. The bot will continue."
        )
        return

    logger.error("Unhandled exception in update handler", exc_info=err)


def main() -> None:
    """Set up the bot and run it."""
    print("🚀 Starting Lunara Bot...")
    logger.info("🚀 Starting Lunara Bot...")

    # Fail fast if required config is missing
    assert config.TELEGRAM_BOT_TOKEN, "❌ TELEGRAM_BOT_TOKEN is not set!"
    assert os.getenv("REDIS_URL"), "❌ REDIS_URL is missing!"

    # --- Database and Schema ---
    db.initialize_database()
    db.migrate_schema()

    # --- Force-enable autotrade via env var ---
    try:
        if os.getenv("ENABLE_AUTOTRADE", "").lower() == "true":
            try:
                admin_id = getattr(config, "ADMIN_USER_ID", None) or int(
                    os.environ.get("ADMIN_USER_ID") or 0
                )
                if admin_id:
                    db.set_autotrade_status(admin_id, True)
                    # Also write a Redis flag for other components
                    try:
                        rc = redis.from_url(
                            os.getenv("REDIS_URL"), decode_responses=True
                        )
                        rc.set(f"autotrade:{admin_id}", "True")
                    except Exception:
                        logger.debug(
                            "Could not write autotrade flag to Redis on startup"
                        )
                    logger.info(
                        "Forced autotrade_enabled via ENABLE_AUTOTRADE env var for admin %s",
                        admin_id,
                    )
            except Exception:
                logger.exception("Error forcing autotrade on startup")
    except Exception:
        logger.exception("Unexpected error evaluating ENABLE_AUTOTRADE")

    # --- Application Setup ---
    persistence = RedisPersistence(redis_url=os.getenv("REDIS_URL"))
    application = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .persistence(persistence)
        .post_init(post_init)
        .build()
    )

    # --- Command Handlers ---
    # Watchdog: guard handler registration so a single missing symbol cannot abort startup
    try:
        application.add_handler(CommandHandler("redischeck", redis_check_command))

    except Exception as _reg_err:
        logger.exception(
            "Handler registration failed; continuing startup: %s", _reg_err
        )

    async def promotion_retry_job(context: ContextTypes.DEFAULT_TYPE) -> None:
        """Background job that processes `promotion_webhook_retry` queue (runs under job_queue).

        This implementation avoids performing blocking Redis network calls on the
        event loop by delegating to asyncio.to_thread. It also traps connection
        failures and exits early so the JobQueue executor doesn't raise.
        """
        MAX_PER_RUN = 10
        MAX_ATTEMPTS = getattr(config, "PROMOTION_WEBHOOK_MAX_ATTEMPTS", 5)

        # Helper to obtain a redis client; return None on failure
        def _get_redis_client():
            try:
                return redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
            except Exception as e:
                logger.debug(f"promotion_retry_job: could not create redis client: {e}")
                return None

        rc = await asyncio.to_thread(_get_redis_client)
        if not rc:
            # Redis temporarily unavailable; skip this run
            return

        for _ in range(MAX_PER_RUN):
            try:
                raw = await asyncio.to_thread(rc.lpop, "promotion_webhook_retry")
            except Exception as e:
                # Network or connection problem — bail out this run and rely on
                # the heartbeat / job scheduler to retry later.
                logger.debug(f"promotion_retry_job: redis lpop failed: {e}")
                break

            if not raw:
                break

            try:
                item = json.loads(raw)
            except Exception:
                # malformed item, skip
                logger.debug(
                    "promotion_retry_job: malformed JSON in retry item; skipping"
                )
                continue

            payload = item.get("payload") or {}
            attempts = int(item.get("attempts", 0) or 0)
            next_try = float(item.get("next_try", 0) or 0)
            now = time.time()
            if next_try and next_try > now:
                # Not ready yet; requeue at tail. Use to_thread to avoid blocking.
                try:
                    await asyncio.to_thread(rc.rpush, "promotion_webhook_retry", raw)
                except Exception as e:
                    logger.debug(
                        f"promotion_retry_job: failed to requeue not-ready item: {e}"
                    )
                # stop processing to avoid spinning
                break

            # attempt send (this is network IO but quick; keep synchronous helper)
            try:
                success, status, body = await asyncio.to_thread(
                    dispatch_promotion_webhook_sync, payload
                )
            except Exception as e:
                logger.debug(
                    f"promotion_retry_job: dispatch_promotion_webhook_sync raised: {e}"
                )
                # treat as transient failure; requeue with backoff
                attempts += 1
                err = str(e)
                if attempts < MAX_ATTEMPTS:
                    backoff = min(2**attempts, 300)
                    item["attempts"] = attempts
                    item["last_error"] = err
                    item["next_try"] = now + backoff
                    try:
                        await asyncio.to_thread(
                            rc.rpush, "promotion_webhook_retry", json.dumps(item)
                        )
                    except Exception:
                        logger.debug(
                            "promotion_retry_job: failed to requeue after dispatch exception"
                        )
                else:
                    try:
                        item["attempts"] = attempts
                        item["last_error"] = err
                        item["failed_at"] = datetime.now(timezone.utc).isoformat()
                        await asyncio.to_thread(
                            rc.lpush, "promotion_webhook_failed", json.dumps(item)
                        )
                        await asyncio.to_thread(
                            rc.ltrim, "promotion_webhook_failed", 0, 9999
                        )
                        try:
                            await asyncio.to_thread(update_retry_metrics, "pending", -1)
                            await asyncio.to_thread(update_retry_metrics, "failed", 1)
                        except Exception:
                            pass
                    except Exception:
                        logger.debug(
                            "promotion_retry_job: failed to move item to failed list after dispatch exception"
                        )
                # continue to next item
                continue

            if success:
                # record success for auditing and update metrics (offload blocking calls)
                try:
                    log_entry = json.dumps(
                        {
                            "event": "promotion_retry_success",
                            "audit_id": payload.get("audit_id"),
                            "trade_id": payload.get("trade_id"),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    await asyncio.to_thread(rc.lpush, "promotion_log", log_entry)
                    await asyncio.to_thread(rc.ltrim, "promotion_log", 0, 499)
                except Exception:
                    pass
                try:
                    await asyncio.to_thread(update_retry_metrics, "pending", -1)
                    await asyncio.to_thread(update_retry_metrics, "total_sent", 1)
                except Exception:
                    pass
                continue
            else:
                attempts += 1
                err = body or "no response"
                if attempts < MAX_ATTEMPTS:
                    backoff = min(2**attempts, 300)
                    item["attempts"] = attempts
                    item["last_error"] = err
                    item["next_try"] = now + backoff
                    try:
                        await asyncio.to_thread(
                            rc.rpush, "promotion_webhook_retry", json.dumps(item)
                        )
                    except Exception:
                        logger.debug(
                            "promotion_retry_job: Failed to requeue failed webhook"
                        )
                else:
                    # give up; move to failed list for manual inspection
                    try:
                        item["attempts"] = attempts
                        item["last_error"] = err
                        item["failed_at"] = datetime.now(timezone.utc).isoformat()
                        await asyncio.to_thread(
                            rc.lpush, "promotion_webhook_failed", json.dumps(item)
                        )
                        await asyncio.to_thread(
                            rc.ltrim, "promotion_webhook_failed", 0, 9999
                        )
                        try:
                            await asyncio.to_thread(update_retry_metrics, "pending", -1)
                            await asyncio.to_thread(update_retry_metrics, "failed", 1)
                        except Exception:
                            pass
                    except Exception:
                        logger.debug(
                            "promotion_retry_job: Failed to move item to failed list"
                        )
        return

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(
        CommandHandler("binance_status", trade.binance_status_command)
    )
    application.add_handler(CommandHandler("about", trade.about_command))
    application.add_handler(
        CommandHandler("quest", quest_command)
    )  # This now handles the main trading logic
    
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("activate", activate_command))
    application.add_handler(CommandHandler("setapi", set_api_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("close", close_command))
    application.add_handler(CommandHandler("review", review_command))
    application.add_handler(CommandHandler("review_export", review_export_command))
    application.add_handler(
        CommandHandler("resonate", resonate_command)
    )  # This now calls the simulation
    application.add_handler(CommandHandler("top_trades", top_trades_command))
    application.add_handler(CommandHandler("referral", referral_command))
    application.add_handler(CommandHandler("leaderboard", leaderboard_command))
    application.add_handler(CommandHandler("myprofile", myprofile_command))
    # /settings remains the admin setter; add /viewsettings for regular users to view their settings
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("viewsettings", view_settings_command))

    # Admin-only: preview and promote estimated quantities from audit table
    async def estimated_quantities_command(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        user_id = update.effective_user.id
        if user_id != getattr(config, "ADMIN_USER_ID", None):
            await update.message.reply_text("Unauthorized.")
            return

        args = context.args if context and context.args else []
        page = 0
        per_page = 5
        if args:
            try:
                page = max(0, int(args[0]) - 1)
            except Exception:
                page = 0

        start = page * per_page
        # Use DB helper to fetch audit rows
        try:
            conn = db.get_db_connection()
            cursor = conn.execute(
                "SELECT id, trade_id, estimated_quantity, source_price, source_trade_size_usdt, confidence, created_at, promoted FROM estimated_quantities_audit ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (per_page, start),
            )
            rows = cursor.fetchall()
        except Exception:
            await update.message.reply_text(
                "Failed to read estimated quantities audit table."
            )
            return

        if not rows:
            await update.message.reply_text("No estimated quantities found.")
            return

        messages = []
        keyboard = []
        for r in rows:
            promoted = r["promoted"] if "promoted" in r.keys() else 0
            messages.append(
                f"ID:{r['id']} trade:{r['trade_id']} est_qty:{r['estimated_quantity']} conf:{r.get('confidence', 0)} promoted:{promoted} ts:{r['created_at']}"
            )
            if not promoted:
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            f"Promote {r['id']}", callback_data=f"promote_est:{r['id']}"
                        )
                    ]
                )

        # Navigation
        nav = [
            InlineKeyboardButton("Prev", callback_data=f"est_page:{max(0, page-1)}"),
            InlineKeyboardButton("Next", callback_data=f"est_page:{page+1}"),
        ]

        reply_markup = (
            InlineKeyboardMarkup(keyboard + [nav])
            if keyboard
            else InlineKeyboardMarkup([nav])
        )
        await update.message.reply_text("\n".join(messages), reply_markup=reply_markup)

    async def estimated_quantities_callback(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        if data.startswith("promote_est:"):
            try:
                audit_id = int(data.split(":", 1)[1])
            except Exception:
                await query.edit_message_text("Invalid promote id")
                return
            # Ask for confirmation
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Confirm Promote",
                            callback_data=f"confirm_promote:{audit_id}",
                        ),
                        InlineKeyboardButton("Cancel", callback_data="cancel"),
                    ]
                ]
            )
            await query.edit_message_text(
                f"Promote estimate id={audit_id}?", reply_markup=keyboard
            )
            return

        if data.startswith("confirm_promote:"):
            try:
                audit_id = int(data.split(":", 1)[1])
            except Exception:
                await query.edit_message_text("Invalid confirmation id")
                return
            # Perform promotion: fetch row, check trades.quantity, then write
            try:
                conn = db.get_db_connection()
                cur = conn.cursor()
                audit_row = cur.execute(
                    "SELECT trade_id, estimated_quantity, promoted FROM estimated_quantities_audit WHERE id = ?",
                    (audit_id,),
                ).fetchone()
                if not audit_row:
                    await query.edit_message_text("Audit entry not found")
                    return
                if audit_row["promoted"]:
                    await query.edit_message_text("Already promoted")
                    return
                trade_id = audit_row["trade_id"]
                est_qty = audit_row["estimated_quantity"]
                # Check current quantity
                trade_row = cur.execute(
                    "SELECT quantity FROM trades WHERE id = ?", (trade_id,)
                ).fetchone()
                if not trade_row:
                    await query.edit_message_text("Trade not found")
                    return
                if trade_row["quantity"] is not None and trade_row["quantity"] > 0:
                    await query.edit_message_text(
                        "Trade already has a quantity, skipping"
                    )
                    return
                # Perform update
                cur.execute(
                    "UPDATE trades SET quantity = ? WHERE id = ?", (est_qty, trade_id)
                )
                cur.execute(
                    "UPDATE estimated_quantities_audit SET promoted = 1 WHERE id = ?",
                    (audit_id,),
                )
                conn.commit()
                # Log promotion to Redis for cross-service auditing
                try:
                    rc = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
                    log_entry = json.dumps(
                        {
                            "audit_id": audit_id,
                            "trade_id": trade_id,
                            "estimated_quantity": est_qty,
                            "promoted_by": update.effective_user.username
                            or str(update.effective_user.id),
                            "confidence": (
                                float(audit_row.get("confidence", 0.0))
                                if isinstance(
                                    audit_row.get("confidence", None), (int, float, str)
                                )
                                else 0.0
                            ),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    rc.lpush("promotion_log", log_entry)
                    rc.ltrim("promotion_log", 0, 499)
                except Exception as e:
                    logger.debug(f"Failed to write promotion log to Redis: {e}")

                # Dispatch webhook asynchronously if enabled
                try:
                    webhook_url = getattr(config, "PROMOTION_WEBHOOK_URL", None)
                    enable_webhook = getattr(config, "ENABLE_PROMOTION_WEBHOOK", False)
                    if enable_webhook and webhook_url:
                        payload = {
                            "event": "promotion",
                            "audit_id": audit_id,
                            "trade_id": trade_id,
                            "estimated_quantity": est_qty,
                            "confidence": (
                                float(audit_row.get("confidence", 0.0))
                                if isinstance(
                                    audit_row.get("confidence", None), (int, float, str)
                                )
                                else 0.0
                            ),
                            "promoted_by": update.effective_user.username
                            or str(update.effective_user.id),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }

                        async def _dispatch_webhook(p):
                            try:
                                loop = asyncio.get_running_loop()

                                def _post():
                                    try:
                                        # Use shared helper to send and enqueue on failure
                                        return send_promotion_webhook(
                                            p, webhook_url=webhook_url, timeout=3
                                        )
                                    except Exception as ex:
                                        logger.debug(f"Promotion webhook error: {ex}")
                                        try:
                                            enqueue_promotion_retry(
                                                p, error=str(ex), attempts=0
                                            )
                                        except Exception:
                                            logger.debug(
                                                "Failed to enqueue failed webhook after exception"
                                            )
                                        return None, str(ex)

                                await loop.run_in_executor(None, _post)
                            except Exception as ex:
                                logger.debug(
                                    f"Failed to dispatch promotion webhook: {ex}"
                                )

                        # schedule background dispatch without awaiting
                        try:
                            asyncio.create_task(_dispatch_webhook(payload))
                        except Exception:
                            # Fallback: run without await in a thread — ensure failures are enqueued
                            try:
                                import threading

                                def _thread_post():
                                    try:
                                        send_promotion_webhook(
                                            payload, webhook_url=webhook_url, timeout=3
                                        )
                                    except Exception as ex:
                                        try:
                                            enqueue_promotion_retry(
                                                payload, error=str(ex), attempts=0
                                            )
                                        except Exception:
                                            logger.debug(
                                                "Failed to enqueue failed webhook from thread after exception"
                                            )

                                threading.Thread(
                                    target=_thread_post, daemon=True
                                ).start()
                            except Exception as ex:
                                logger.debug(f"Failed to spawn webhook thread: {ex}")
                except Exception as e:
                    logger.debug(f"Webhook dispatch preparation failed: {e}")

                await query.edit_message_text(
                    f"Promoted estimate {audit_id} -> trade {trade_id} (qty={est_qty})"
                )
            except Exception as e:
                conn.rollback()
                await query.edit_message_text(f"Failed to promote: {e}")
            finally:
                conn.close()
            return

        if data == "cancel":
            await query.edit_message_text("Cancelled")
            return

    from telegram.ext import CallbackQueryHandler

    application.add_handler(
        CommandHandler("estimated_quantities", estimated_quantities_command)
    )
    application.add_handler(CallbackQueryHandler(estimated_quantities_callback))

    async def promotion_log_command(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        user_id = update.effective_user.id
        if user_id != getattr(config, "ADMIN_USER_ID", None):
            await update.message.reply_text("Unauthorized.")
            return

        args = context.args if context and context.args else []
        page = 0
        per_page = 10
        if args:
            try:
                page = max(0, int(args[0]) - 1)
            except Exception:
                page = 0

        start = page * per_page
        end = start + per_page - 1
        try:
            rc = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
            items = rc.lrange("promotion_log", start, end) or []
        except Exception:
            await update.message.reply_text(
                "Redis unavailable or promotion_log missing."
            )
            return

        if not items:
            await update.message.reply_text("No promotions found.")
            return

        messages = []
        for it in items:
            try:
                e = json.loads(it)
                messages.append(
                    f"audit:{e.get('audit_id')} trade:{e.get('trade_id')} qty:{e.get('estimated_quantity')} by:{e.get('promoted_by')} ts:{e.get('timestamp')}"
                )
            except Exception:
                messages.append(str(it))

        keyboard = []
        if page > 0:
            keyboard.append(
                InlineKeyboardButton(
                    "Prev", callback_data=f"promotion_log_page:{page-1}"
                )
            )
        keyboard.append(
            InlineKeyboardButton("Next", callback_data=f"promotion_log_page:{page+1}")
        )

        await update.message.reply_text(
            "\n".join(messages), reply_markup=InlineKeyboardMarkup([keyboard])
        )

    # Register promotion log command
    application.add_handler(CommandHandler("promotion_log", promotion_log_command))

    async def clear_logs_command(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Admin-only: clear common Redis log lists and optionally truncate the on-disk log file.

        Usage:
          /clear_logs            - show what would be cleared
          /clear_logs confirm    - actually clear Redis lists
          /clear_logs file confirm - also truncate logs/lunara_bot.log on disk
        """
        user_id = update.effective_user.id
        if user_id != getattr(config, "ADMIN_USER_ID", None):
            await update.message.reply_text("Unauthorized.")
            return

        args = context.args if context and context.args else []
        do_confirm = len(args) and args[-1].lower() == "confirm"
        do_file = len(args) and "file" in [a.lower() for a in args]

        redis_keys = [
            "promotion_log",
            "promotion_webhook_failed",
            "promotion_webhook_retry",
            "trade_issues",
            "autosuggest_audit",
            "autotrade:skipped_events",
        ]

        if not do_confirm:
            await update.message.reply_text(
                "This will clear the following Redis lists: ``{}``\nRun `/clear_logs confirm` to proceed.\nAdd the word `file` to also truncate the on-disk log file.".format(
                    ", ".join(redis_keys)
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # Perform deletion
        deleted = {}
        try:
            rc = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
        except Exception:
            rc = None

        if rc:
            for k in redis_keys:
                try:
                    cnt = rc.delete(k)
                    deleted[k] = cnt
                except Exception as e:
                    deleted[k] = f"error:{e}"
        else:
            for k in redis_keys:
                deleted[k] = "redis_unavailable"

        file_result = None
        if do_file:
            try:
                log_path = os.path.join("logs", "lunara_bot.log")
                # Truncate the file safely
                with open(log_path, "w", encoding="utf-8") as fh:
                    fh.truncate(0)
                file_result = "truncated"
            except Exception as e:
                file_result = f"error:{e}"

        reply = [f"Cleared Redis keys (result):"]
        for k, v in deleted.items():
            reply.append(f" - {k}: {v}")
        if do_file:
            reply.append(f"On-disk log file: {file_result}")

        await update.message.reply_text("\n".join(reply))

    application.add_handler(CommandHandler("clear_logs", clear_logs_command))

    async def refresh_cache_command(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Admin-only: clear in-memory cached bot_data used by /status so it fetches fresh data.

        Usage: /refresh_cache
        """
        user_id = update.effective_user.id
        if user_id != getattr(config, "ADMIN_USER_ID", None):
            await update.message.reply_text("Unauthorized.")
            return

        keys_to_clear = ["all_prices", "checked_symbols", "market_state"]
        cleared = []
        for k in keys_to_clear:
            if k in context.bot_data:
                try:
                    del context.bot_data[k]
                    cleared.append(k)
                except Exception:
                    pass

        await update.message.reply_text(
            f"Cleared in-memory cache keys: {', '.join(cleared) if cleared else 'none'}"
        )

    application.add_handler(CommandHandler("refresh_cache", refresh_cache_command))

    async def status_command_bot(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        user_id = update.effective_user.id
        if user_id != getattr(config, "ADMIN_USER_ID", None):
            await update.message.reply_text("Unauthorized.")
            return

        parts = []

        # Redis checks
        rc = None
        try:
            rc = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
            rc.ping()
            trade_issues_count = rc.llen("trade_issues")
            promotion_log_count = rc.llen("promotion_log")
            parts.append(
                f"Redis: OK — trade_issues={trade_issues_count}, promotion_log={promotion_log_count}"
            )
        except Exception as e:
            parts.append(f"Redis: FAILED — {e}")

        # DB checks
        try:
            conn = db.get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(1) as c FROM trades")
            trades_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(1) as c FROM estimated_quantities_audit")
            try:
                audit_count = cur.fetchone()[0]
            except Exception:
                audit_count = 0
            parts.append(
                f"DB: OK — trades={trades_count}, estimated_audit={audit_count}"
            )
        except Exception as e:
            parts.append(f"DB: FAILED — {e}")

        # Webhook config
        webhook_url = getattr(config, "PROMOTION_WEBHOOK_URL", None)
        webhook_enabled = getattr(config, "ENABLE_PROMOTION_WEBHOOK", False)
        parts.append(
            f"Webhook: {'ENABLED' if webhook_enabled and webhook_url else 'DISABLED'}"
        )

        # Recent activity brief: include small snippets for quick glance
        try:
            if rc:
                recent_trade_issues = rc.lrange("trade_issues", 0, 4) or []
                recent_promotions = rc.lrange("promotion_log", 0, 4) or []
            else:
                recent_trade_issues = []
                recent_promotions = []

            parts.append(
                f"Recent trade_issues: {len(recent_trade_issues)} (showing up to 5)"
            )
            for it in recent_trade_issues[:5]:
                try:
                    e = json.loads(it)
                    when = e.get("ts") or e.get("timestamp") or e.get("ts")
                    parts.append(
                        f" - TID:{snip(e.get('trade_id'))} user:{snip(e.get('user_id'))} sym:{snip(e.get('symbol'))} qty:{snip(e.get('quantity'))} ts:{snip(when)}"
                    )
                except Exception:
                    parts.append(f" - {snip(str(it))}")

            parts.append(
                f"Recent promotions: {len(recent_promotions)} (showing up to 5)"
            )
            for it in recent_promotions[:5]:
                try:
                    e = json.loads(it)
                    parts.append(
                        f" - audit:{snip(e.get('audit_id'))} trade:{snip(e.get('trade_id'))} qty:{snip(e.get('estimated_quantity'))} by:{snip(e.get('promoted_by'))} ts:{snip(e.get('timestamp'))}"
                    )
                except Exception:
                    parts.append(f" - {snip(str(it))}")
        except Exception:
            parts.append("Recent activity: n/a")

        message = "\n".join(parts)
        await update.message.reply_text(f"Lunessa Status:\n{message}")

    application.add_handler(CommandHandler("botstatus", status_command_bot))

    # Admin-only: view recent trade issues recorded in Redis by the monitoring job
    async def trade_issues_command(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        user_id = update.effective_user.id
        if user_id != getattr(config, "ADMIN_USER_ID", None):
            await update.message.reply_text("Unauthorized.")
            return

        try:
            rc = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
        except Exception:
            await update.message.reply_text("Redis unavailable.")
            return

        # Pagination: support ?page=N or /trade_issues N
        args = context.args if context and context.args else []
        page = 0
        per_page = 10
        if args:
            try:
                page = int(args[0]) - 1 if int(args[0]) > 0 else 0
            except Exception:
                page = 0

        start = page * per_page
        end = start + per_page - 1
        try:
            items = rc.lrange("trade_issues", start, end) or []
        except Exception:
            await update.message.reply_text("Failed to read trade_issues from Redis.")
            return

        if not items:
            await update.message.reply_text("No trade issues found.")
            return

        messages = []
        for it in items:
            try:
                entry = json.loads(it)
            except Exception:
                entry = {"raw": it}
            ts = entry.get("ts") or entry.get("timestamp") or None
            when = datetime.fromtimestamp(ts).isoformat() if ts else "unknown"
            messages.append(
                f"- trade_id={entry.get('trade_id')} user={entry.get('user_id')} symbol={entry.get('symbol')} qty={entry.get('quantity')} ts={when}"
            )

        text = "\n".join(messages)

        # Inline navigation
        keyboard = []
        if page > 0:
            keyboard.append(
                InlineKeyboardButton(
                    "Prev", callback_data=f"trade_issues:page:{page-1}"
                )
            )
        keyboard.append(
            InlineKeyboardButton("Next", callback_data=f"trade_issues:page:{page+1}")
        )
        reply_markup = InlineKeyboardMarkup([keyboard])

        await update.message.reply_text(
            f"Trade issues (page {page+1}):\n{text}", reply_markup=reply_markup
        )

    async def trade_issues_callback(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        # Handle inline button callbacks
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        parts = data.split(":")
        if len(parts) >= 3 and parts[0] == "trade_issues" and parts[1] == "page":
            try:
                page = int(parts[2])
            except Exception:
                page = 0
            # Simulate calling the command with page+1
            mock_update = update
            mock_context = context
            # Reuse the command handler logic by calling it with adjusted args
            mock_context.args = [str(page + 1)]
            await trade_issues_command(mock_update, mock_context)

    application.add_handler(CommandHandler("trade_issues", trade_issues_command))
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"^trade_issues:\d+$"), trade_issues_command
        )
    )
    from telegram.ext import CallbackQueryHandler

    application.add_handler(CallbackQueryHandler(trade_issues_callback))
    application.add_handler(CommandHandler("subscribe", subscribe_command))
    application.add_handler(CommandHandler("papertrade", papertrade_command))
    application.add_handler(CommandHandler("verifypayment", verifypayment_command))
    application.add_handler(CommandHandler("confirm_payment", confirm_payment_command))
    application.add_handler(CommandHandler("pay", pay_command))
    application.add_handler(CommandHandler("safety", safety_command))
    application.add_handler(CommandHandler("hubspeedy", hubspeedy_command))
    application.add_handler(CommandHandler("linkbinance", linkbinance_command))
    application.add_handler(CommandHandler("learn", learn_command))
    application.add_handler(CommandHandler("ask", ask_command))
    application.add_handler(CommandHandler("usercount", trade.usercount_command))
    application.add_handler(CommandHandler("addcoins", addcoins_command))
    # /buy command intentionally removed: autotrade executes self-trades for users
    
    application.add_handler(CommandHandler("wallet", wallet_command))
    application.add_handler(CommandHandler("checked", checked_command))
    application.add_handler(CommandHandler("cleanslips", clean_slips_command))
    application.add_handler(CommandHandler("audit_recent", audit_recent_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))

    # Admin commands for retry queue
    async def retry_queue_command(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        user_id = update.effective_user.id
        if user_id != getattr(config, "ADMIN_USER_ID", None):
            await update.message.reply_text("Unauthorized.")
            return
        try:
            rc = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
            total = rc.llen("promotion_webhook_retry")
            sample = rc.lrange("promotion_webhook_retry", 0, 9) or []
        except Exception:
            await update.message.reply_text("Redis unavailable.")
            return

        msgs = [f"Pending: {total} (showing up to 10)"]
        for i, it in enumerate(sample):
            try:
                j = json.loads(it)
                msgs.append(
                    "{i}: audit={audit} attempts={attempts} next_try={next}".format(
                        i=i,
                        audit=j.get("payload", {}).get("audit_id"),
                        attempts=j.get("attempts"),
                        next=j.get("next_try"),
                    )
                )
            except Exception:
                msgs.append(f"{i}: {it}")
        await update.message.reply_text("\n".join(msgs))

    async def retry_dispatch_command(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        user_id = update.effective_user.id
        if user_id != getattr(config, "ADMIN_USER_ID", None):
            await update.message.reply_text("Unauthorized.")
            return
        if not context.args:
            await update.message.reply_text("Usage: /retry_dispatch <index>")
            return
        try:
            idx = int(context.args[0])
        except Exception:
            await update.message.reply_text("Index must be an integer.")
            return
        try:
            rc = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
            item = rc.lindex("promotion_webhook_retry", idx)
            if not item:
                await update.message.reply_text("No item at that index.")
                return
            obj = json.loads(item)
            payload = obj.get("payload") or {}
            success, status, body = dispatch_promotion_webhook_sync(payload)
            await update.message.reply_text(
                f"Retry result: success={success} status={status} info={str(body)[:300]}"
            )
            if success:
                # remove the item at idx by using a Lua script (atomic) or LSET+LREM trick
                try:
                    marker = "__TO_DELETE__" + str(time.time())
                    rc.lset("promotion_webhook_retry", idx, marker)
                    rc.lrem("promotion_webhook_retry", 1, marker)
                except Exception:
                    logger.debug("Failed to remove retried item from queue")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def retry_flush_command(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        user_id = update.effective_user.id
        if user_id != getattr(config, "ADMIN_USER_ID", None):
            await update.message.reply_text("Unauthorized.")
            return
        # confirm with a second argument 'confirm'
        if not context.args or context.args[0] != "confirm":
            await update.message.reply_text(
                "This will clear the retry queue. To confirm, run: /retry_flush confirm"
            )
            return
        try:
            rc = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
            rc.delete("promotion_webhook_retry")
            await update.message.reply_text("Retry queue cleared.")
        except Exception as e:
            await update.message.reply_text(f"Failed to clear queue: {e}")

    application.add_handler(CommandHandler("retry_queue", retry_queue_command))
    application.add_handler(CommandHandler("retry_dispatch", retry_dispatch_command))
    application.add_handler(CommandHandler("retry_flush", retry_flush_command))

    async def retry_stats_command(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        user_id = update.effective_user.id
        if user_id != getattr(config, "ADMIN_USER_ID", None):
            await update.message.reply_text("Unauthorized.")
            return
        try:
            rc = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
        except Exception:
            await update.message.reply_text("Redis unavailable.")
            return

        try:
            pending = rc.llen("promotion_webhook_retry")
            failed = rc.llen("promotion_webhook_failed")
            promotion_log = rc.llen("promotion_log")
            last_failed = rc.lindex("promotion_webhook_failed", 0)
            last_failed_ts = None
            if last_failed:
                try:
                    j = json.loads(last_failed)
                    last_failed_ts = j.get("failed_at") or j.get("payload", {}).get(
                        "timestamp"
                    )
                except Exception:
                    last_failed_ts = str(last_failed)[:120]
            msg = [
                f"pending={pending}",
                f"failed={failed}",
                f"promotions_logged={promotion_log}",
            ]
            if last_failed_ts:
                msg.append(f"last_failed={last_failed_ts}")
            await update.message.reply_text("\n".join(msg))
        except Exception as e:
            await update.message.reply_text(f"Failed to collect stats: {e}")

    application.add_handler(CommandHandler("retry_stats", retry_stats_command))

    async def whoami_command(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Admin-only diagnostic: show resolved admin id, Binance client status, and Redis connectivity."""
        user_id = update.effective_user.id
        if user_id != getattr(config, "ADMIN_USER_ID", None):
            await update.message.reply_text("Unauthorized.")
            return

        lines = []
        lines.append(
            f"Configured ADMIN_USER_ID: {getattr(config, 'ADMIN_USER_ID', None)}"
        )
        lines.append(f"Module-level ADMIN_ID: {ADMIN_ID}")

        # Binance client status
        try:
            import trade as _trade

            available = getattr(_trade, "BINANCE_AVAILABLE", False)
            init_err = getattr(_trade, "BINANCE_INIT_ERROR", None)
            lines.append(f"BINANCE_AVAILABLE: {available}")
            if init_err:
                lines.append(f"BINANCE_INIT_ERROR: {init_err}")
        except Exception as e:
            lines.append(f"BINANCE status: error reading trade module: {e}")

        # Redis connectivity
        try:
            rc = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
            try:
                pong = await asyncio.to_thread(
                    lambda: rc.ping() if hasattr(rc, "ping") else False
                )
            except Exception:
                pong = False
            try:
                pending = await asyncio.to_thread(rc.llen, "promotion_webhook_retry")
            except Exception:
                pending = "n/a"
            lines.append(f"Redis ping: {pong}")
            lines.append(f"promotion_webhook_retry pending: {pending}")
        except Exception as e:
            lines.append(f"Redis: error creating client: {e}")

        await update.message.reply_text("\n".join(lines))

    application.add_handler(CommandHandler("whoami", whoami_command))

    async def test_slip_command(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Admin-only diagnostic to inspect and attempt to decrypt a specific slip key.

        Usage:
          /test_slip <slip_key_or_trade_id> [delete]

        If `delete` is provided, the slip will be removed after the diagnostic.
        """
        user_id = update.effective_user.id
        if user_id != getattr(config, "ADMIN_USER_ID", None):
            await update.message.reply_text("Unauthorized.")
            return

        if not context.args:
            await update.message.reply_text(
                "Usage: /test_slip <slip_key_or_trade_id> [delete]\nExample: /test_slip 1756556374179 or /test_slip trade:1756556374179:data delete"
            )
            return

        raw_key = context.args[0]
        do_delete = len(context.args) > 1 and context.args[1].lower() in (
            "delete",
            "purge",
        )

        # Normalize lookup key
        if ":" not in raw_key:
            lookup_key = f"trade:{raw_key}:data"
        else:
            lookup_key = raw_key

        import slip_manager as _sm

        lines = [f"Inspecting slip key: {lookup_key}"]

        # Attempt to read raw value from Redis/fallback
        try:
            client = _sm.get_redis_client()
            if client:
                raw_val = client.get(lookup_key)
            else:
                raw_val = _sm.fallback_cache.get(lookup_key)
        except Exception as e:
            raw_val = None
            lines.append(f"Error reading from Redis/fallback: {e}")

        if raw_val is None:
            lines.append("No value found for that key.")
            await update.message.reply_text("\n".join(lines))
            return

        # Show metadata about raw value
        try:
            if isinstance(raw_val, (bytes, bytearray)):
                b = bytes(raw_val)
            elif isinstance(raw_val, str):
                b = raw_val.encode()
            else:
                b = str(raw_val).encode()
            lines.append(f"Raw value length: {len(b)} bytes")
            # show first 120 bytes as base64-safe hex snippet
            snippet = b[:120]
            try:
                import base64

                snippet_b64 = base64.b64encode(snippet).decode()
                lines.append(f"Raw snippet (base64, first 120 bytes): {snippet_b64}")
            except Exception:
                lines.append(f"Raw snippet (hex): {snippet[:120].hex()}")
        except Exception as e:
            lines.append(f"Failed to examine raw bytes: {e}")

        # Attempt decryption using slip_manager.get_fernet()
        try:
            fernet = _sm.get_fernet()
            if not fernet:
                lines.append(
                    "Fernet instance not available (missing SLIP_ENCRYPTION_KEY or derivation failed)."
                )
            else:
                try:
                    # Ensure bytes
                    enc = b
                    try:
                        dec = fernet.decrypt(enc)
                        text = dec.decode("utf-8", errors="replace").strip()
                        lines.append("Decryption: SUCCESS")
                        # Try to parse JSON
                        try:
                            import json as _json

                            parsed = _json.loads(text)
                            import pprint

                            pretty = pprint.pformat(parsed)
                            # Truncate long output
                            if len(pretty) > 3000:
                                pretty = pretty[:3000] + "\n...truncated..."
                            lines.append("Decrypted payload:")
                            lines.append(pretty)
                        except Exception:
                            # Not JSON; show raw text (truncated)
                            display = (
                                text
                                if len(text) <= 2000
                                else text[:2000] + "\n...truncated..."
                            )
                            lines.append("Decrypted text:")
                            lines.append(display)
                    except Exception as e:
                        # Provide exception details for debugging
                        lines.append(f"Decryption: FAILED — {type(e).__name__}: {e}")
                except Exception as e:
                    lines.append(f"Decryption attempt raised: {e}")
        except Exception as e:
            lines.append(f"Failed to prepare Fernet: {e}")

        # If requested, delete the slip after inspection
        if do_delete:
            try:
                _sm.delete_slip(lookup_key)
                lines.append("Slip deleted (per request).")
            except Exception as e:
                lines.append(f"Failed to delete slip: {e}")

        # Send the diagnostic message (truncate if too large)
        out = "\n".join(lines)
        if len(out) > 4000:
            out = out[:3990] + "\n...truncated..."
        await update.message.reply_text(out)

    application.add_handler(CommandHandler("test_slip", test_slip_command))

    async def diagnose_slips_command(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """
        Scans Redis for all slips grouped by trade id, attempts decryption using
        opinionated `slip_manager` helpers, and reports a concise summary.

        Optional actions: quarantine | purge

        Usage: /diagnose_slips [quarantine|purge]
        """
        user_id = update.effective_user.id
        if user_id != ADMIN_ID:
            await update.message.reply_text("Unauthorized.")
            return

        args = context.args or []
        action = None
        dry_run = False
        # Accept flexible ordering: /diagnose_slips [quarantine|purge] [--dry-run]
        for a in args:
            aa = a.lower().strip()
            if aa in ("quarantine", "purge") and action is None:
                action = aa
            elif aa in ("--dry-run", "-n", "dry-run", "--noop"):
                dry_run = True
            elif aa in ("--csv", "--export-csv", "--csv-file"):
                csv_export = True
            else:
                # ignore unknown tokens to be lenient
                continue

        if action and action not in ("quarantine", "purge"):
            await update.message.reply_text(
                "Invalid action. Use 'quarantine' or 'purge'."
            )
            return

        await update.message.reply_text(
            "Starting slip diagnostic scan... This may take a moment. I'll reply with a summary when finished."
        )

        total_trades = 0
        decrypted_trades = 0
        failed_slips = []

        try:
            # Strong opinion: prefer slip_manager APIs for Redis/fallback handling
            client = slip_manager.get_redis_client()
            fernet = slip_manager.get_fernet()
            if not fernet:
                await update.message.reply_text(
                    "Encryption key (SLIP_ENCRYPTION_KEY or BINANCE_ENCRYPTION_KEY) is not configured — aborting diagnostic."
                )
                return

            # Collect trade ids from Redis or fallback cache
            if client:
                try:
                    raw_keys = await asyncio.to_thread(
                        lambda: list(client.scan_iter("trade:*"))
                    )
                except Exception:
                    raw_keys = []
            else:
                raw_keys = [
                    k
                    for k in slip_manager.fallback_cache.keys()
                    if k.startswith("trade:")
                ]

            trade_ids = set()
            for k in raw_keys:
                try:
                    ks = k.decode() if isinstance(k, (bytes, bytearray)) else str(k)
                except Exception:
                    ks = str(k)
                parts = ks.split(":")
                if len(parts) >= 2 and parts[0] == "trade":
                    trade_ids.add(parts[1])

            # Use slip_manager.list_all_slips() to get successfully decrypted slips
            decrypted_slips_list = slip_manager.list_all_slips()
            decrypted_ids = set()
            for s in decrypted_slips_list:
                key = s.get("key")
                if isinstance(key, str) and key.startswith("trade:"):
                    parts = key.split(":")
                    if len(parts) >= 2:
                        decrypted_ids.add(parts[1])

            total_trades = len(trade_ids)

            # For each trade id that wasn't returned as decrypted, try a targeted check
            for tid in sorted(trade_ids):
                if tid in decrypted_ids:
                    decrypted_trades += 1
                    continue

                # Not part of decrypted set — attempt per-field decryption to capture precise errors
                keys_for_tid = []
                if client:
                    try:
                        keys_for_tid = await asyncio.to_thread(
                            lambda: list(client.scan_iter(f"trade:{tid}:*"))
                        )
                    except Exception:
                        keys_for_tid = []
                else:
                    keys_for_tid = [
                        k
                        for k in slip_manager.fallback_cache.keys()
                        if k.startswith(f"trade:{tid}:")
                    ]

                # Always include canonical data key first
                candidate_keys = [f"trade:{tid}:data"] + [
                    k.decode() if isinstance(k, (bytes, bytearray)) else str(k)
                    for k in keys_for_tid
                ]

                success = False
                for ck in candidate_keys:
                    try:
                        # Use slip_manager.get_and_decrypt_slip which encapsulates decryption + fallback logic
                        result = await asyncio.to_thread(
                            slip_manager.get_and_decrypt_slip, ck
                        )
                        if result is not None:
                            decrypted_trades += 1
                            success = True
                            break
                        else:
                            # If key exists but result is None, try to detect cause by inspecting raw value
                            if client:
                                raw_val = await asyncio.to_thread(client.get, ck)
                            else:
                                raw_val = slip_manager.fallback_cache.get(ck)
                            if raw_val is None:
                                # No value found for this key; keep scanning other keys
                                continue
                            # If there was a raw value but get_and_decrypt_slip returned None, attempt direct Fernet decrypt to capture error
                            try:
                                raw_bytes = (
                                    raw_val.encode()
                                    if isinstance(raw_val, str)
                                    else raw_val
                                )
                                await asyncio.to_thread(fernet.decrypt, raw_bytes)
                                # If decrypt succeeds here, treat as decrypted (paranoid)
                                decrypted_trades += 1
                                success = True
                                break
                            except Exception as e:
                                failed_slips.append(
                                    {
                                        "key": ck,
                                        "error": str(e),
                                        "type": type(e).__name__,
                                    }
                                )
                                # continue trying other keys for this trade id
                    except Exception as e:
                        failed_slips.append(
                            {"key": ck, "error": str(e), "type": type(e).__name__}
                        )

                if not success:
                    # If none of the fields decrypted, record a summary failure for the trade id
                    # If there were no candidate keys (shouldn't happen), record a generic message
                    if not candidate_keys:
                        failed_slips.append(
                            {
                                "key": f"trade:{tid}:*",
                                "error": "no keys found",
                                "type": "KeyError",
                            }
                        )

            # Optionally quarantine or purge failures. Support dry-run simulation.
            action_taken = 0
            planned_actions = []
            if action and failed_slips:
                for failure in failed_slips:
                    k = failure.get("key")
                    try:
                        if dry_run:
                            planned_actions.append({"action": action, "key": k})
                        else:
                            if action == "quarantine":
                                # Prefer rename for Redis, fallback to moving in fallback_cache
                                if client:
                                    try:
                                        await asyncio.to_thread(
                                            client.rename, k, f"archive:{k}"
                                        )
                                    except Exception:
                                        # Fallback: copy value to archive and delete original
                                        try:
                                            val = await asyncio.to_thread(client.get, k)
                                            if val is not None:
                                                await asyncio.to_thread(
                                                    client.set, f"archive:{k}", val
                                                )
                                                await asyncio.to_thread(
                                                    client.delete, k
                                                )
                                        except Exception:
                                            pass
                                else:
                                    v = slip_manager.fallback_cache.pop(k, None)
                                    if v is not None:
                                        slip_manager.fallback_cache[f"archive:{k}"] = v
                            elif action == "purge":
                                # Use slip_manager.delete_slip which is opinionated about trade ids
                                try:
                                    await asyncio.to_thread(slip_manager.delete_slip, k)
                                except Exception:
                                    if client:
                                        try:
                                            await asyncio.to_thread(client.delete, k)
                                        except Exception:
                                            pass
                                    else:
                                        slip_manager.fallback_cache.pop(k, None)
                        action_taken += 1
                    except Exception as e:
                        logger.debug(f"Failed to perform action {action} on {k}: {e}")

            # Build summary
            summary_lines = ["Slip Diagnostic Report:\n"]
            summary_lines.append(f"Total trades found: {total_trades}")
            summary_lines.append(
                f"Trades with at least one decrypted field: {decrypted_trades}"
            )
            summary_lines.append(
                f"Trades with decryption failures (sampled fields): {len(failed_slips)}"
            )
            if action:
                summary_lines.append(
                    f"Action requested: {action} — applied to {action_taken} items."
                )

            if failed_slips:
                summary_lines.append("--- Sample Failures (up to 5) ---")
                for fs in failed_slips[:5]:
                    summary_lines.append(f"Key: {fs.get('key')}")
                    summary_lines.append(f"Error: {fs.get('type')}: {fs.get('error')}")
                    summary_lines.append("")
                if len(failed_slips) > 5:
                    summary_lines.append(
                        f"...and {len(failed_slips) - 5} more failures."
                    )

                # If CSV export requested or large result set, generate CSV and send as document
                try:
                    if csv_export or len(failed_slips) > 50:
                        import csv as _csv
                        import io as _io

                        sio = _io.StringIO()
                        writer = _csv.writer(sio)
                        writer.writerow(["key", "error_type", "error_message"])
                        for fs in failed_slips:
                            writer.writerow(
                                [fs.get("key"), fs.get("type"), fs.get("error")]
                            )
                        csv_bytes = sio.getvalue().encode("utf-8")
                        bio = _io.BytesIO(csv_bytes)
                        bio.seek(0)
                        # Send as a document to the admin who requested the command
                        try:
                            await update.message.reply_document(
                                document=bio,
                                filename="diagnose_slips_failures.csv",
                                caption="Full failure list (CSV)",
                            )
                        except Exception:
                            # Fallback: send as a plain message if document sending fails
                            await update.message.reply_text(
                                "Failed to send CSV file as document; here's a short sample:"
                            )
                            await update.message.reply_text(
                                "\n".join(summary_lines[:40])
                            )
                except Exception as _csv_err:
                    logger.debug(f"Failed to prepare/send CSV export: {_csv_err}")

            await update.message.reply_text("\n".join(summary_lines))

        except Exception as e:
            logger.exception("diagnose_slips_command failed: %s", e)
            await update.message.reply_text(
                f"Diagnostic failed: {type(e).__name__}: {e}"
            )

    application.add_handler(CommandHandler("diagnose_slips", diagnose_slips_command))

    # --- Message Handlers ---
    application.add_error_handler(_global_error_handler)

    # Add the slip handler for text messages starting with 'SLIP:'
    try:
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.Regex(r"(?i)^SLIP:"),
                slip_handler,
            )
        )

    except Exception as _reg_err:
        # Log and continue; handlers that failed to register will not be available,
        # but the bot process should remain running so the admin can debug remotely.
        logger.exception(
            "Handler registration failed; continuing startup: %s", _reg_err
        )

    # ---
    # Set up background jobs ---
    job_queue = application.job_queue

    # Register background retry job: run every 15 seconds
    try:
        job_queue.run_repeating(promotion_retry_job, interval=15, first=15)
    except Exception:
        logger.debug("Failed to schedule promotion_retry_job via job_queue")
    # Schedule the auto-scan job to run every 10 minutes (600 seconds).
    job_queue.run_repeating(
        trade.scheduled_monitoring_job,
        interval=config.AI_TRADE_INTERVAL_MINUTES * 60,
        first=10,
    )  # This job now handles all monitoring
    # Schedule the daily summary job to run at 8:00 AM UTC
    job_queue.run_daily(
        send_daily_status_summary,
        time=datetime(1, 1, 1, 8, 0, 0, tzinfo=timezone.utc).time(),
    )
    job_queue.run_repeating(
        autotrade_jobs.autotrade_cycle, interval=900, first=10
    )  # 15 minutes
    job_queue.run_repeating(autotrade_jobs.monitor_autotrades, interval=60, first=10)

    # Add a lightweight heartbeat job: pings Redis and logs a simple metric every 5 minutes.
    async def heartbeat_job(context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            # Redis client used here may be a blocking client; run blocking calls
            # in a thread to avoid blocking the event loop and to ensure the
            # job callback is awaitable (JobQueue expects an awaitable callback).
            try:
                rc = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
            except Exception:
                rc = None

            if rc is None:
                logger.debug("Heartbeat: Redis client unavailable")
                return

            try:
                pong = await asyncio.to_thread(
                    lambda: rc.ping() if hasattr(rc, "ping") else False
                )
            except Exception:
                pong = False

            try:
                pending = await asyncio.to_thread(
                    lambda: rc.llen("promotion_webhook_retry")
                )
            except Exception:
                pending = None

            logger.info(
                "HEARTBEAT: redis_ping=%s promotion_retry_pending=%s", pong, pending
            )
        except Exception as e:
            logger.debug("Heartbeat error: %s", e)

    # schedule heartbeat every 5 minutes (300s) after first run in 30s
    try:
        job_queue.run_repeating(heartbeat_job, interval=300, first=30)
    except Exception:
        logger.debug("Failed to schedule heartbeat job via job_queue")

    # Daily Facebook auto-post job (admin-controlled via env vars)
    async def daily_facebook_post_job(context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            # Determine configured target time and whether auto-posting is enabled
            token = os.getenv("FB_ACCESS_TOKEN")
            page_id = os.getenv("FACEBOOK_PAGE_ID")
            auto_post = os.getenv("AUTO_POST_FACEBOOK", "false").lower() in (
                "1",
                "true",
                "yes",
            )
            if not token or not page_id:
                logger.debug("Facebook credentials not configured; skipping daily post")
                return

            # For each admin-defined user (for now only ADMIN_ID), compose a summary and post
            try:
                import facebook_poster
                import performance_reviews
            except Exception as e:
                logger.exception("Failed to import review/facebook modules: %s", e)
                return

            target_user = str(getattr(config, "ADMIN_USER_ID", None) or "")
            if not target_user:
                logger.debug("No ADMIN_USER_ID configured; skipping FB post")
                return

            reviews = performance_reviews.get_reviews(target_user)
            if not reviews:
                logger.debug("No reviews to post for user %s", target_user)
                return

            msg = facebook_poster.format_facebook_post(target_user, reviews)

            # Append a marketing hashtag (derived from app name)
            hashtag = os.getenv("MARKETING_HASHTAG", "#LunessaSignals")
            if hashtag and not msg.endswith("\n"):
                msg = msg + "\n\n" + hashtag

            logger.info(
                "Daily Facebook post composed for user %s; auto_post=%s",
                target_user,
                auto_post,
            )
            if auto_post:
                ok = facebook_poster.post_to_facebook(token, page_id, msg)
                logger.info("Facebook post result: %s", ok)
            else:
                # Not auto-posting: log and save to file for manual review
                logger.info(
                    "AUTO_POST_FACEBOOK disabled; saving scheduled post to logs"
                )
                logger.info(msg)
        except Exception as e:
            logger.exception("daily_facebook_post_job failed: %s", e)

    # Schedule the daily Facebook posting job. Time can be configured with FACEBOOK_POST_TIME_UTC (HH:MM)
    try:
        fb_time = os.getenv("FACEBOOK_POST_TIME_UTC", "08:30")
        hh, mm = (int(x) for x in fb_time.split(":"))
        job_queue.run_daily(
            daily_facebook_post_job,
            time=datetime(1, 1, 1, hh, mm, 0, tzinfo=timezone.utc).time(),
        )
    except Exception:
        logger.debug(
            "Failed to schedule daily_facebook_post_job; check FACEBOOK_POST_TIME_UTC format"
        )

    logger.info(
        "Starting bot with market monitor and AI trade monitor jobs scheduled..."
    )

    # --- Run the Bot ---
    try:
        logger.info("Starting bot polling...")
        application.run_polling()
    except Exception as e:
        # Log any exception raised by run_polling for debugging
        logger.exception("application.run_polling() raised an exception: %s", e)
        # re-raise so external wrappers/tests can see it
        raise


async def clean_slips_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Admin command to list and optionally delete Redis trade slips."""
    user_id = update.effective_user.id
    if user_id != getattr(config, "ADMIN_USER_ID", None):
        await update.message.reply_text("This is an admin-only command.")
        return

    if not context.args:
        # List all slips
        slips = slip_manager.list_all_slips()
        if not slips:
            await update.message.reply_text("No trade slips found in Redis.")
            return

        message = "📜 **Current Redis Trade Slips:**\n\n"
        for slip in slips:
            key = slip["key"]
            data = slip.get("data", {})
            symbol = data.get("symbol", "N/A")
            timestamp = data.get("timestamp", "N/A")
            message += f"- `{key}` (Symbol: {symbol}, Time: {timestamp})\n"
        message += "\nTo delete a slip, use: `/cleanslips <full_slip_key>`"
        await update.message.reply_text(message, parse_mode="Markdown")
    else:
        # Delete a specific slip
        slip_key_to_delete = context.args[0]
        try:
            # Ensure the key is bytes as Redis keys are bytes
            if not slip_key_to_delete.startswith("trade:"):
                slip_key_to_delete = "trade:" + slip_key_to_delete

            slip_manager.cleanup_slip(slip_key_to_delete.encode())
            await update.message.reply_text(
                f"✅ Slip `{slip_key_to_delete}` deleted from Redis."
            )
        except Exception as e:
            logger.error(f"Error deleting slip {slip_key_to_delete}: {e}")
            await update.message.reply_text(
                f"⚠️ Failed to delete slip `{slip_key_to_delete}`. Error: {e}"
            )


async def _redis_pubsub_listener(app: Application) -> None:
    """Async listener that reacts to toggle events published to 'autotrade:notify'."""
    try:
        import redis.asyncio as aioredis
    except ImportError:
        logger.warning(
            "redis.asyncio not available; pubsub listener for real-time toggles is disabled."
        )
        return

    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        logger.warning("No REDIS_URL configured; pubsub listener disabled.")
        return

    # Keep a persistent connection and ensure we don't use async-timeouts
    # in a way that raises "Timeout should be used inside a task" during
    # shutdown. If the pubsub loop is cancelled, exit cleanly.
    reconnect_delay = 5
    while True:
        try:
            rc = aioredis.from_url(redis_url, decode_responses=True)
            pubsub = await rc.pubsub(ignore_subscribe_messages=True).__aenter__()
            try:
                await pubsub.subscribe("autotrade:notify")
                logger.info(
                    "Subscribed to autotrade:notify channel for instant toggles."
                )

                async for message in pubsub.listen():
                    try:
                        data = message.get("data", "{}")
                        payload = json.loads(data)
                        logger.info("Received autotrade notification: %s", payload)

                        # Schedule immediate jobs to pick up the toggle change.
                        app.job_queue.run_once(autotrade_jobs.autotrade_cycle, when=1)
                        app.job_queue.run_once(
                            autotrade_jobs.monitor_autotrades, when=1
                        )
                    except Exception as e:
                        logger.error(f"Error processing pubsub message: {e}")
            finally:
                # Ensure we close the pubsub context cleanly
                try:
                    await pubsub.unsubscribe("autotrade:notify")
                except Exception:
                    pass
                try:
                    await rc.pubsub(ignore_subscribe_messages=True).__aexit__(
                        None, None, None
                    )
                except Exception:
                    # Best-effort close; ignore errors during shutdown
                    pass

        except Exception as e:
            # redis.asyncio may or may not expose an 'exceptions' namespace depending on
            # the installed package version. Do a safe lookup for ConnectionError and
            # re-raise to the generic handler if it isn't the type we expect.
            conn_err_cls = None
            try:
                conn_err_cls = getattr(aioredis, "exceptions", None)
                if conn_err_cls:
                    conn_err_cls = getattr(conn_err_cls, "ConnectionError", None)
            except Exception:
                conn_err_cls = None

            if conn_err_cls and isinstance(e, conn_err_cls):
                logger.error(
                    f"Redis pubsub connection error: {e}. Reconnecting in {reconnect_delay} seconds..."
                )
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(60, reconnect_delay * 2)
                continue
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(60, reconnect_delay * 2)
        except asyncio.CancelledError:
            logger.info("Redis pubsub listener task cancelled; exiting listener loop.")
            break
        except Exception as e:
            logger.critical(
                f"Redis pubsub listener terminated unexpectedly: {e}", exc_info=True
            )
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(60, reconnect_delay * 2)


if __name__ == "__main__":
    main()
