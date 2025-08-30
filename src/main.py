

import os
import logging
import redis
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
import google.generativeai as genai
from .slip_parser import parse_slip, SlipParseError
from . import trade_executor
from . import redis_validator
from .Simulation import resonance_engine
from . import config
import trade
import slip_manager # Import slip_manager
from .handlers import *
from .jobs import *
from .decorators import require_tier
from .modules import db_access as db
from datetime import datetime, timezone, timedelta
import autotrade_jobs
from . import autotrade_db

logger = logging.getLogger(__name__)

import asyncio

## Gemini API keys are now managed in autotrade_jobs.py for multi-key support and fallback

async def redis_check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            await context.bot.send_message(chat_id=admin_id, text=f"👥 Total users: <b>{user_count}</b>", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Failed to send user count to admin: {e}")

    for user_id in all_user_ids:
        open_trades = db.get_open_trades(user_id)
        if not open_trades:
            continue # Skip users with no open trades

        # ...existing code...

async def quest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for the /crypto command. Calls the trade module."""
    user_id = update.effective_user.id
    user_tier = db.get_user_tier_db(user_id)
    if user_tier != 'PREMIUM':
        # Free users: Only show RSI
        symbol = context.args[0].upper() if context.args else None
        if not symbol:
            await update.message.reply_text("Please specify a symbol. Usage: /quest SYMBOL", parse_mode='Markdown')
            return
        rsi = trade.get_rsi(symbol)
        if rsi is None:
            await update.message.reply_text(f"Could not fetch RSI for {symbol}.")
            return
        await update.message.reply_text(f"RSI for {symbol}: `{rsi:.2f}`\nUpgrade to Premium for full analysis.", parse_mode='Markdown')
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

    if tier != 'FREE' and expires_str:
        try:
            expires_dt = datetime.fromisoformat(expires_str).astimezone(timezone.utc)
            now_utc = datetime.now(timezone.utc)
            if expires_dt > now_utc:
                days_remaining = (expires_dt - now_utc).days
                expiry_date_formatted = expires_dt.strftime('%d %b %Y')
                subscription_message += f"- Expires: **{expiry_date_formatted}** ({days_remaining} days left)\n"
            else:
                subscription_message += "- Status: **Expired**\n"
        except (ValueError, TypeError):
            subscription_message += "- Expiry: *Not set*\n" # Handle parsing errors
            
    subscription_message += "\n" + ("-"*20) + "\n\n"


    open_trades = db.get_open_trades(user_id)
    watched_items = db.get_watched_items_by_user(user_id)

    user_tier = db.get_user_tier_db(user_id)
    
    # Get active slips from Redis
    active_slips = slip_manager.list_all_slips()
    active_slip_symbols = {slip['data']['symbol'] for slip in active_slips if 'data' in slip and 'symbol' in slip['data']}
    active_slip_keys = {slip['key'] for slip in active_slips}

    # Filter open_trades to only include those actively monitored by Redis slips
    monitored_trades = [trade_item for trade_item in open_trades if trade_item['coin_symbol'] in active_slip_symbols]
    orphaned_trades = [trade_item for trade_item in open_trades if trade_item['coin_symbol'] not in active_slip_symbols]

    if not monitored_trades and not watched_items and not orphaned_trades:
        # Prepend subscription status even if there are no trades
        await update.message.reply_text(subscription_message + "You have no open quests, watched symbols, or orphaned trades. Use /quest to find an opportunity.", parse_mode='Markdown')
        return

    message = ""

    # --- Get all prices from the job's cache ---
    prices = {}
    cached_prices_data = context.bot_data.get('all_prices', {})
    if cached_prices_data:
        cache_timestamp = cached_prices_data.get('timestamp')
        # Cache is valid if it's less than 125 seconds old (job runs every 60s)
        if cache_timestamp and (datetime.now(timezone.utc) - cache_timestamp).total_seconds() < 125:
            prices = cached_prices_data.get('prices', {})
            logger.info(f"Using cached prices for /status for user {user_id}.")
        else:
            logger.warning(f"Price cache for user {user_id} is stale. Displaying last known data.")

    if monitored_trades:
        message += "**Your Open Quests (Monitored):**\n"
        for trade_item in monitored_trades:
            symbol = trade_item['coin_symbol']
            buy_price = trade_item['buy_price']
            current_price = prices.get(symbol)
            trade_id = trade_item['id']

            message += f"\n🔹 **{symbol}** (ID: {trade_id})"

            if current_price:
                pnl_percent = ((current_price - buy_price) / buy_price) * 100
                pnl_emoji = "📈" if pnl_percent >= 0 else "📉"
                message += (
                    f"\n   {pnl_emoji} P/L: `{pnl_percent:+.2f}%`"
                    f"\n   Bought: `${buy_price:,.8f}`"
                    f"\n   Current: `${current_price:,.8f}`"
                )
                if user_tier == 'PREMIUM':
                    tp_price = trade_item['take_profit_price']
                    stop_loss = trade_item['stop_loss_price']
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
            symbol = trade_item['coin_symbol']
            trade_id = trade_item['id']
            message += f"\n🔸 **{symbol}** (ID: {trade_id})"
        message += "\n" # Add a newline for spacing

    if watched_items:
        message += "\n🔭 **Your Watched Symbols:**\n"
        for item in watched_items:
            # Calculate time since added
            add_time = datetime.strptime(item['add_timestamp'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            time_watching = datetime.now(timezone.utc) - add_time
            hours, remainder = divmod(time_watching.total_seconds(), 3600)
            minutes, _ = divmod(remainder, 60)
            message += f"\n🔸 **{item['coin_symbol']}** (*Watching for {int(hours)}h {int(minutes)}m*)"

    # The send_premium_message wrapper is overly complex; a direct reply is cleaner.
    await update.message.reply_text(subscription_message + message, parse_mode='Markdown')

async def resonate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs LunessaSignals's quantum resonance simulation and sends the results."""
    user_id = update.effective_user.id
    symbol = None
    if context.args:
        symbol = context.args[0].upper()
        await update.message.reply_text(f"Attuning my quantum senses to the vibrations of **{symbol}**... Please wait. 🔮", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("Attuning my quantum senses to the general market vibration... Please wait. 🔮")

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

        narrative = results['narrative']
        metric_plot_path = results['metric_plot']
        clock_plot_path = results['clock_plot']

        # Send the narrative text
        await update.message.reply_text(narrative, parse_mode=ParseMode.MARKDOWN)

        # Send the plots
        with open(metric_plot_path, 'rb') as photo1, open(clock_plot_path, 'rb') as photo2:
            await update.message.reply_photo(photo=photo1, caption="Soul Waveform Analysis")
            await update.message.reply_photo(photo=photo2, caption="Clock Phase Distortions")

    except Exception as e:
        logger.error(f"Error running resonance simulation for user {user_id}: {e}", exc_info=True)
        await update.message.reply_text("The cosmic energies are scrambled. I could not generate a resonance report at this time.")
    finally:
        # Clean up the generated plot files
        if metric_plot_path and os.path.exists(metric_plot_path):
            os.remove(metric_plot_path)
        if clock_plot_path and os.path.exists(clock_plot_path):
            os.remove(clock_plot_path)

async def safety_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Static handler for the /safety command."""
    await update.message.reply_text(
        "Protect your capital like a sacred treasure. Never invest more than you are willing to lose. "
        "A stop-loss is your shield in the volatile realm of crypto."
    )

async def hubspeedy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Static handler for the /hubspeedy command."""
    await update.message.reply_text("For more advanced tools and community, check out our main application! [Link Here]")

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for the /balance command. Calls the trade module."""
    await trade.balance_command(update, context)

async def import_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for the /import command. Calls the trade module."""
    await trade.import_last_trade_command(update, context)

async def close_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Closes an open trade. Usage: /close <trade_id>"""
    user_id = update.effective_user.id
    try:
        trade_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("Please provide a valid trade ID.\nUsage: `/close <trade_id>`", parse_mode='Markdown')
        return

    trade_to_close = db.get_trade_by_id(trade_id=trade_id, user_id=user_id)

    if not trade_to_close:
        await update.message.reply_text("Could not find an open trade with that ID under your name. Check `/status`.", parse_mode='Markdown')
        return

    symbol = trade_to_close['coin_symbol']
    buy_price = trade_to_close['buy_price']
    current_price = trade.get_current_price(symbol)
    if current_price is None:
        await update.message.reply_text(f"Could not fetch the current price for {symbol} to close the trade. Please try again.")
        return

    pnl_percentage = ((current_price - buy_price) / buy_price) * 100
    win_loss = 'win' if pnl_percentage > 0 else 'loss' if pnl_percentage < 0 else 'breakeven'
    close_reason = 'manual'
    closed_by = update.effective_user.username or update.effective_user.first_name

    success = db.close_trade(
        trade_id=trade_id,
        user_id=user_id,
        sell_price=current_price,
        close_reason=close_reason,
        win_loss=win_loss,
        pnl_percentage=pnl_percentage,
        closed_by=closed_by
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
                data = slip.get('data', {})
                if data.get('symbol') == symbol:
                    slip_key = slip['key']
                    break
            if slip_key:
                slip_manager.cleanup_slip(slip_key)
                await update.message.reply_text(f"Slip data for {symbol} has been cleaned up.")
            else:
                await update.message.reply_text(f"No slip data found for {symbol} to clean up.")
        except Exception as e:
            logger.error(f"Slip cleanup failed: {e}")
            await update.message.reply_text("Trade closed, but slip cleanup failed.")
    else:
        await update.message.reply_text("Failed to close the trade.")

async def wallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the user's current spot wallet balances on Binance."""
    user_id = update.effective_user.id
    mode, _ = db.get_user_trading_mode_and_balance(user_id)
    is_admin = user_id == config.ADMIN_USER_ID

    if mode != 'LIVE' and not is_admin:
        await update.message.reply_text("This command is for LIVE mode only. Your paper wallet is managed separately via /balance.")
        return

    await update.message.reply_text("Retrieving your spot wallet balances from Binance... 🏦")

    try:
        # Admin/creator/father bypasses API key check
        if is_admin:
            balances = trade.get_all_spot_balances(config.ADMIN_USER_ID)
        else:
            balances = trade.get_all_spot_balances(user_id)
        if balances is None:
            if is_admin:
                await update.message.reply_text("Admin wallet retrieval failed. Please check Binance connectivity.", parse_mode='Markdown')
            else:
                await update.message.reply_text("Could not retrieve balances. Please ensure your API keys are set correctly with `/setapi`.", parse_mode='Markdown')
            return
        if not balances:
            await update.message.reply_text("Your spot wallet appears to be empty.")
            return

        # Fetch all prices at once for valuation
        all_tickers = trade.client.get_all_tickers()
        prices = {item['symbol']: float(item['price']) for item in all_tickers}

        valued_assets = []
        total_usdt_value = 0.0

        for balance in balances:
            asset = balance['asset']
            total_balance = float(balance['free']) + float(balance['locked'])

            if asset.upper() in ['USDT', 'BUSD', 'USDC', 'FDUSD', 'TUSD']:
                usdt_value = total_balance
            else:
                pair = f"{asset}USDT"
                price = prices.get(pair)
                usdt_value = (total_balance * price) if price else 0

            if usdt_value > 1.0:  # Only show assets worth more than $1
                valued_assets.append({'asset': asset, 'balance': total_balance, 'usdt_value': usdt_value})
                if asset.upper() not in ['USDT', 'BUSD', 'USDC', 'FDUSD', 'TUSD']:
                    total_usdt_value += usdt_value

        # Add USDT itself to the total value at the end
        total_usdt_value += next((b['usdt_value'] for b in valued_assets if b['asset'] == 'USDT'), 0)

        # Sort by USDT value, descending
        valued_assets.sort(key=lambda x: x['usdt_value'], reverse=True)

        message = "💎 **Your Spot Wallet Holdings:**\n\n"
        for asset_info in valued_assets:
            balance_str = f"{asset_info['balance']:,.8f}".rstrip('0').rstrip('.')
            message += f"  - **{asset_info['asset']}**: `{balance_str}` (~${asset_info['usdt_value']:,.2f})\n"

        message += f"\n*Estimated Total Value:* `${total_usdt_value:,.2f}` USDT"

        await update.message.reply_text(message, parse_mode='Markdown')

    except trade.TradeError as e:
        await update.message.reply_text(f"⚠️ **Error!**\n\n*Reason:* `{e}`", parse_mode='Markdown')

async def import_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Imports all significant holdings from Binance wallet as new quests."""
    user_id = update.effective_user.id
    mode, _ = db.get_user_trading_mode_and_balance(user_id)

    if mode != 'LIVE':
        await update.message.reply_text("Upgrade to Premium to use this feature.")
        return

    await update.message.reply_text("Scanning your Binance wallet to import all significant holdings as quests... 🔎 This may take a moment.")

    try:
        balances = trade.get_all_spot_balances(user_id)
        if not balances:
            await update.message.reply_text("Your spot wallet appears to be empty. Nothing to import.")
            return

        # Fetch all prices at once
        all_tickers = trade.client.get_all_tickers()
        prices = {item['symbol']: float(item['price']) for item in all_tickers}

        imported_count = 0
        skipped_count = 0
        message_lines = []

        for balance in balances:
            asset = balance['asset']
            total_balance = float(balance['free']) + float(balance['locked'])
            symbol = f"{asset}USDT"

            if asset.upper() in ['USDT', 'BUSD', 'USDC', 'FDUSD', 'TUSD']:
                continue

            price = prices.get(symbol)
            if not price:
                continue

            usdt_value = total_balance * price
            if usdt_value < 10.0:
                continue

            if db.is_trade_open(user_id, symbol):
                skipped_count += 1
                continue

            settings = db.get_user_effective_settings(user_id)
            stop_loss_price = price * (1 - settings['STOP_LOSS_PERCENTAGE'] / 100)
            take_profit_price = price * (1 + settings['PROFIT_TARGET_PERCENTAGE'] / 100)

            db.log_trade(
                user_id=user_id, coin_symbol=symbol, buy_price=price,
                stop_loss=stop_loss_price, take_profit=take_profit_price,
                mode='LIVE', trade_size_usdt=usdt_value, quantity=total_balance
            )
            imported_count += 1
            message_lines.append(f"  ✅ Imported **{symbol}** (~${usdt_value:,.2f})")

        summary_message = "✨ **Import Complete!** ✨\n\n"
        if message_lines:
            summary_message += "\n".join(message_lines) + "\n\n"
        summary_message += f"*Summary:*\n- New Quests Started: `{imported_count}`\n- Already Tracked: `{skipped_count}`\n\n"
        summary_message += "Use /status to see your newly managed quests."

        await update.message.reply_text(summary_message, parse_mode='Markdown')

    except trade.TradeError as e:
        await update.message.reply_text(f"⚠️ **Error!**\n\n*Reason:* `{e}`", parse_mode='Markdown')


async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Places a live buy order. Premium feature.
    Usage: /buy <SYMBOL> <USDT_AMOUNT>
    """
    user_id = update.effective_user.id
    mode, _ = db.get_user_trading_mode_and_balance(user_id)

    is_admin = user_id == config.ADMIN_USER_ID
    if mode != 'LIVE' and not is_admin:
        await update.message.reply_text("Upgrade to Premium to use this feature.")
        return

    try:
        symbol = context.args[0].upper()
        usdt_amount = float(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("Please specify a symbol and amount.\nUsage: `/buy PEPEUSDT 11`", parse_mode='Markdown')
        return

    if db.is_trade_open(user_id, symbol):
        await update.message.reply_text(f"You already have an open quest for {symbol}. Use /status to see it.")
        return

    await update.message.reply_text(f"Preparing to embark on a **LIVE** quest for **{symbol}** with **${usdt_amount:.2f}**...", parse_mode='Markdown')

    try:
        # Admin/creator/father bypasses API key check
        if is_admin:
            live_balance = float('inf')
        else:
            live_balance = trade.get_account_balance(user_id, 'USDT')
        if not is_admin and (live_balance is None or live_balance < usdt_amount):
            await update.message.reply_text(f"Your live USDT balance (`${live_balance:.2f}`) is insufficient for this quest.")
            return

        # Place the live order
        if is_admin:
            order, entry_price, quantity = trade.place_buy_order(config.ADMIN_USER_ID, symbol, usdt_amount)
        else:
            order, entry_price, quantity = trade.place_buy_order(user_id, symbol, usdt_amount)

        # Log the successful trade
        settings = db.get_user_effective_settings(user_id)
        stop_loss_price = entry_price * (1 - settings['STOP_LOSS_PERCENTAGE'] / 100)
        take_profit_price = entry_price * (1 + settings['PROFIT_TARGET_PERCENTAGE'] / 100)
        db.log_trade(user_id=user_id, coin_symbol=symbol, buy_price=entry_price,
                     stop_loss=stop_loss_price, take_profit=take_profit_price,
                     mode='LIVE', trade_size_usdt=usdt_amount, quantity=quantity)

        await update.message.reply_text(f"🚀 **Live Quest Started!**\n\nSuccessfully bought **{quantity:,.4f} {symbol}** at `${entry_price:,.8f}`.\n\nI will now monitor this quest for you. Use /status to see its progress.", parse_mode='Markdown')

    except trade.TradeError as e:
        await update.message.reply_text(f"⚠️ **Quest Failed!**\n\n*Reason:* `{e}`", parse_mode='Markdown')

async def checked_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows which symbols the AI has checked recently."""
    user_id = update.effective_user.id

    if user_id != config.ADMIN_USER_ID:
        await update.message.reply_text("This is an admin-only command.")
        return

    checked_symbols_log = context.bot_data.get('checked_symbols', [])
    if not checked_symbols_log:
        await update.message.reply_text("The AI has not checked any symbols yet.")
        return

    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)

    # Filter for the last hour and get unique symbols
    recent_checks = sorted(list({symbol for ts, symbol in checked_symbols_log if ts > one_hour_ago}))

    # Cleanup old entries from the log to prevent it from growing indefinitely
    two_hours_ago = datetime.now(timezone.utc) - timedelta(hours=2)
    context.bot_data['checked_symbols'] = [(ts, symbol) for ts, symbol in checked_symbols_log if ts > two_hours_ago]

    if not recent_checks:
        await update.message.reply_text("The AI has not checked any symbols in the last hour.")
        return

    message = "📈 **AI Oracle's Recent Scans (Last Hour):**\n\n" + ", ".join(f"`{s}`" for s in recent_checks)
    await update.message.reply_text(message, parse_mode='Markdown')

async def review_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reviews the user's completed trade performance."""
    user_id = update.effective_user.id
    closed_trades = db.get_closed_trades(user_id)

    if not closed_trades:
        await update.message.reply_text("You have no completed trades to review. Close a trade using `/close <id>`.", parse_mode='Markdown')
        return

    wins = 0
    losses = 0
    total_profit_percent = 0.0
    best_trade = None
    worst_trade = None
    # Use -inf and inf to correctly handle all possible P/L values
    best_pnl = -float('inf')
    worst_pnl = float('inf')

    for t in closed_trades:
        profit_percent = ((t['sell_price'] - t['buy_price']) / t['buy_price']) * 100

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

    message = (
        f"""🌟 **LunessaSignals Performance Review** 🌟

**Completed Quests:** {total_trades}
**Victories (Wins):** {wins}
**Setbacks (Losses):** {losses}
**Win Rate:** {win_rate:.2f}%

**Average P/L:** `{avg_pnl_percent:,.2f}%`
"""
    )

    if best_trade and worst_trade:
        message += (
            f"\n"
            f"**Top Performers:**\n"
            f"🚀 **Best Quest:** {best_trade['coin_symbol']} (`{best_pnl:+.2f}%`)\n"
            f"💔 **Worst Quest:** {worst_trade['coin_symbol']} (`{worst_pnl:+.2f}%`)\n"
        )

    message += "\nKeep honing your skills, seeker. The market's rhythm is complex."
    await update.message.reply_text(message, parse_mode='Markdown')

async def top_trades_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the user's top 3 most profitable closed trades."""
    user_id = update.effective_user.id
    top_trades = db.get_top_closed_trades(user_id, limit=3)

    if not top_trades:
        await update.message.reply_text("You have no completed profitable quests to rank. Close a winning trade to enter the Hall of Fame!", parse_mode='Markdown')
        return

    message = "🏆 **Your Hall of Fame** 🏆\n\n_Here are your most legendary victories:_\n\n"
    rank_emojis = ["🥇", "🥈", "🥉"]

    for i, trade in enumerate(top_trades):
        emoji = rank_emojis[i] if i < len(rank_emojis) else "🔹"
        message += f"{emoji} **{trade['coin_symbol']}**: `{trade['pnl_percent']:+.2f}%`\n"

    message += "\nMay your future quests be even more glorious!"
    await update.message.reply_text(message, parse_mode='Markdown')

async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the bot owner's referral link and information."""
    if not config.ADMIN_REFERRAL_CODE:
        await update.message.reply_text("The referral program is not configured for this bot.")
        return

    referral_link = f"https://www.binance.com/en/activity/referral-entry/CPA?ref={config.ADMIN_REFERRAL_CODE}"

    message = (
        f"""🤝 **Invite Friends, Earn Together!** 🤝

Refer friends to buy crypto on Binance, and we both get rewarded!

**The Deal:**
When your friend signs up using the link below and buys over $50 worth of crypto, you both receive a **$100 trading fee rebate voucher**.

**Your Tools to Share:**

🔗 **Referral Link:**
`{referral_link}`

🏷️ **Referral Code:**
`{config.ADMIN_REFERRAL_CODE}`

Share the link or code with your friends to start earning. Thank you for supporting the LunessaSignals project!"""
    )
    await update.message.reply_text(message, parse_mode='Markdown', disable_web_page_preview=True)

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the global leaderboard of top trades."""
    top_trades = db.get_global_top_trades(limit=3)

    if not top_trades:
        await update.message.reply_text("The Hall of Legends is still empty. No legendary quests have been completed yet!", parse_mode='Markdown')
        return

    message = "🏆 **Hall of Legends: Global Top Quests** 🏆\n\n_These are the most glorious victories across the realm:_\n\n"
    rank_emojis = ["🥇", "🥈", "🥉"]

    for i, trade in enumerate(top_trades):
        emoji = rank_emojis[i] if i < len(rank_emojis) else "🔹"
        user_id = trade['user_id']
        user_name = "A mysterious adventurer" # Default name
        try:
            chat = await context.bot.get_chat(user_id)
            user_name = chat.first_name
        except Exception as e:
            logger.warning(f"Could not fetch user name for {user_id} for leaderboard: {e}")

        message += f"{emoji} **{trade['coin_symbol']}**: `{trade['pnl_percent']:+.2f}%` (by {user_name})\n"

    message += "\nWill your name be etched into legend?"
    await update.message.reply_text(message, parse_mode='Markdown')

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
    user_tier = db.get_user_tier(user_id)
    settings = db.get_user_effective_settings(user_id)
    trading_mode, paper_balance = db.get_user_trading_mode_and_balance(user_id)

    username = update.effective_user.username or "(not set)"
    autotrade = "Enabled" if db.get_autotrade_status(user_id) else "Disabled"
    message = (
        f"""*Your Profile*

*User ID:* `{user_id}`
*Username:* @{username}
*Tier:* {user_tier}
*Trading Mode:* {trading_mode}
*Autotrade:* {autotrade}"""
    )
    if trading_mode == "LIVE":
        # Optionally, fetch and show real USDT balance here
        message += f"\n*USDT Balance:* (see /wallet)"
    else:
        message += f"\n*Paper Balance:* `${paper_balance:,.2f}`"
    message += "\n\n*Custom Settings:*"
    message += f"\n- RSI Buy: {settings['RSI_BUY_THRESHOLD']}"
    message += f"\n- RSI Sell: {settings['RSI_SELL_THRESHOLD']}"
    message += f"\n- Stop Loss: {settings['STOP_LOSS_PERCENTAGE']}%"
    message += f"\n- Trailing Activation: {settings['TRAILING_PROFIT_ACTIVATION_PERCENT']}%"
    message += f"\n- Trailing Drop: {settings['TRAILING_STOP_DROP_PERCENT']}%"
    if user_tier == 'PREMIUM':
        message += f"\n- Bollinger Band Width: {settings.get('BOLLINGER_BAND_WIDTH', 2.0)}"
        message += f"\n- MACD Signal Threshold: {settings.get('MACD_SIGNAL_THRESHOLD', 0)}"
    await update.message.reply_text(message, parse_mode='Markdown')

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Allows Premium users to view and customize their trading settings."""
    user_id = update.effective_user.id
    user_tier = db.get_user_tier(user_id)

    def escape_markdown(text):
        import re
        return re.sub(r'([_*-[`()~>#+-.=|{{}}!]])', r'\\\1', text)

    if user_tier != 'PREMIUM':
        await update.message.reply_text("Upgrade to Premium to use this feature.")
        return

    # If no args, show current settings and usage
    if not context.args:
        settings = db.get_user_effective_settings(user_id)
        message = (
            f"""⚙️ **Your Custom Trading Settings** ⚙️

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
        )

        await update.message.reply_text(escape_markdown(message), parse_mode='MarkdownV2')
        return

    # Logic to set a value
    try:
        setting_name = context.args[0].lower()
        value_str = context.args[1].lower()
    except IndexError:
        await update.message.reply_text(escape_markdown("Invalid format. Usage: `/settings <name> <value>`"), parse_mode='MarkdownV2')
        return

    try:
        valid_settings = list(db.SETTING_TO_COLUMN_MAP.keys()) + ['trade_size']
        if setting_name not in valid_settings:
            await update.message.reply_text(escape_markdown(f"Unknown setting '{setting_name}'. Valid settings are: {', '.join(valid_settings)}"), parse_mode='MarkdownV2')
            return

        if setting_name == 'trade_size':
            if value_str == 'reset':
                db.update_user_setting(user_id, 'trade_size', 5.0)
                await update.message.reply_text(escape_markdown("Trade size reset to $5.00 (minimum)."), parse_mode='MarkdownV2')
                return
            try:
                new_value = float(value_str)
            except ValueError:
                await update.message.reply_text(escape_markdown(f"Invalid value '{value_str}'. Please provide a number (e.g., 8.5) or 'reset'."), parse_mode='MarkdownV2')
                return
            if new_value < 5.0:
                await update.message.reply_text(escape_markdown("Trade size must be at least $5.00."), parse_mode='MarkdownV2')
                return
            db.update_user_setting(user_id, 'trade_size', new_value)
            await update.message.reply_text(escape_markdown(f"✅ Successfully updated trade size to **${new_value:.2f}**."), parse_mode='MarkdownV2')
            return

        # Existing settings logic
        new_value = None if value_str == 'reset' else float(value_str)
        if new_value is not None and new_value <= 0:
            await update.message.reply_text(escape_markdown("Value must be a positive number."), parse_mode='MarkdownV2')
            return
        db.update_user_setting(user_id, setting_name, new_value)
        await update.message.reply_text(escape_markdown(f"✅ Successfully updated **{setting_name}** to **{value_str}**."), parse_mode='MarkdownV2')
    except ValueError:
        await update.message.reply_text(escape_markdown(f"Invalid value '{value_str}'. Please provide a number (e.g., 8.5) or 'reset'."), parse_mode='MarkdownV2')

async def autotrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to control the AI autotrading feature."""
    user_id = update.effective_user.id
    if user_id != config.ADMIN_USER_ID:
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
            parse_mode=ParseMode.HTML
        )
        return

    sub_command = context.args[0].lower()
    if sub_command == 'on':
        db.set_autotrade_status(user_id, True)
        await update.message.reply_text(
            """🤖 <b>AI Autotrade has been ENABLED.</b>

The bot will now scan for strong buy signals and execute trades for you automatically. You will receive notifications for every action taken.

To disable, use <code>/autotrade off</code>.""",
            parse_mode=ParseMode.HTML
        )
    elif sub_command == 'off':
        db.set_autotrade_status(user_id, False)
        await update.message.reply_text(
            """🤖 <b>AI Autotrade has been DISABLED.</b>

The bot will no longer execute trades automatically. You are now in manual mode.

To enable again, use <code>/autotrade on</code>.""",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text("Invalid command. Use <code>/autotrade on</code> or <code>/autotrade off</code>.", parse_mode=ParseMode.HTML)

async def addcoins_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Premium command to add or reset coins for AI monitoring."""
    user_id = update.effective_user.id
    if user_id != config.ADMIN_USER_ID and db.get_user_tier_db(user_id) != 'PREMIUM':
        await update.message.reply_text("Upgrade to Premium to use this feature.")
        return

    args = context.args
    if not args:
        coins = getattr(config, "AI_MONITOR_COINS", [])
        coins_str = ", ".join(coins) if coins else "None"
        await update.message.reply_text(
            f"Current monitored coins: {coins_str}\nUsage: /addcoins OMbtc, ARBUSDT, ... or /addcoins reset",
            parse_mode='Markdown'
        )
        return

    if args[0].lower() == "reset":
        config.AI_MONITOR_COINS = [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "ARBUSDT", "PEPEUSDT", "DOGEUSDT", "SHIBUSDT"
        ]
        await update.message.reply_text("AI_MONITOR_COINS has been reset to default.")
        return

    # Add coins (comma or space separated)
    coins_to_add = []
    for arg in args:
        coins_to_add += [c.strip().upper() for c in arg.replace(",", " ").split() if c.strip()]
    # Remove duplicates, add to config
    current_coins = set(getattr(config, "AI_MONITOR_COINS", []))
    new_coins = current_coins.union(coins_to_add)
    config.AI_MONITOR_COINS = list(new_coins)
    coins_str = ", ".join(config.AI_MONITOR_COINS)
    await update.message.reply_text(f"Updated monitored coins: {coins_str}")

async def set_api_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("API key linking is not available for free users. Upgrade to Premium.")

async def activate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Activation is a Premium feature.")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Broadcast is a Premium feature.")

async def papertrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Paper trading is a Premium feature.")

async def verifypayment_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verifies payment and upgrades user tier."""
    user_id = update.effective_user.id
    if user_id != config.ADMIN_USER_ID:
        await update.message.reply_text("This command can only be used by the bot administrator.")
        return

    try:
        target_telegram_id = int(context.args[0])
        payment_reference = context.args[1]
        tier_name = context.args[2].upper() # e.g., BASIC, PRO, ELITE
        duration_months = int(context.args[3]) # e.g., 1, 3, 12
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: `/verifypayment <TELEGRAM_ID> <PAYMENT_REFERENCE> <TIER> <DURATION_MONTHS>`\nExample: `/verifypayment 123456789 BKASH_TRX12345 PRO 1`", parse_mode=ParseMode.MARKDOWN)
        return

    # Validate tier
    if tier_name not in config.SUBSCRIPTION_TIERS:
        await update.message.reply_text(f"Invalid tier: {tier_name}. Available tiers: {', '.join(config.SUBSCRIPTION_TIERS.keys())}")
        return

    # Calculate expiry date
    expiry_date = datetime.now(timezone.utc) + timedelta(days=30 * duration_months)

    # Update user tier in DB
    db.update_user_subscription(target_telegram_id, tier=tier_name, expires=expiry_date.strftime('%Y-%m-%d %H:%M:%S'))

    await update.message.reply_text(
        f"""✅ Payment verified for user `{target_telegram_id}` (Ref: `{payment_reference}`).\nTier upgraded to **{tier_name}** until `{expiry_date.strftime('%Y-%m-%d %H:%M:%S UTC')}`.""",
        parse_mode=ParseMode.MARKDOWN
    )

    # Notify the user whose tier was updated
    try:
        await context.bot.send_message(
            chat_id=target_telegram_id,
            text=f"""🎉 Your LunessaSignals subscription has been upgraded to **{tier_name}**!\nIt is valid until `{expiry_date.strftime('%Y-%m-%d %H:%M:%S UTC')}`.\nThank you for your support!""",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.warning(f"Could not send notification to user {target_telegram_id} about tier upgrade: {e}")

async def confirm_payment_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to confirm payment and activate a standard subscription."""
    if update.effective_user.id != config.ADMIN_USER_ID:
        await update.message.reply_text("⛔ You are not authorized to perform this action.")
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
    expires_str = expiry_date.strftime('%Y-%m-%d %H:%M:%S')

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
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.warning(f"Could not send subscription activation notification to user {target_user_id}: {e}")


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
    await update.message.reply("✅ Once verified, your subscription will be activated and you'll receive a confirmation message via Telegram.")

async def usercount_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("User count is a Premium feature.")


# ---
# Restore previous /ask command logic ---
async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /ask command using Gemini AI for Premium users."""
    user_id = update.effective_user.id
    user_tier = db.get_user_tier_db(user_id)
    if user_tier != 'PREMIUM':
        await update.message.reply_text("Upgrade to Premium to use the AI Oracle.")
        return
    question = " ".join(context.args) if context.args else None
    if not question:
        await update.message.reply_text("Please provide a question. Usage: /ask Should I buy ARBUSDT now?")
        return
    await update.message.reply_text("Consulting the AI Oracle... Please wait.")
    try:
        from autotrade_jobs import get_ai_suggestions
        answer = await get_ai_suggestions(question)
        if answer:
            await update.message.reply_text(f"🔮 AI Oracle says:\n\n{answer}")
        else:
            await update.message.reply_text("The AI Oracle could not answer at this time.")
    except Exception as e:
        logger.error(f"AI Oracle error: {e}")
        await update.message.reply_text("The AI Oracle could not answer at this time.")


# ---
# Placeholder Command Handlers ---
async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays subscription tiers and benefits, or the user's current status."""
    user_id = update.effective_user.id
    tier, expires_str = db.get_user_subscription_db(user_id)

    if tier != 'FREE' and expires_str:
        try:
            # Using fromisoformat for robust parsing
            expires_dt = datetime.fromisoformat(expires_str).astimezone(timezone.utc)
            now_utc = datetime.now(timezone.utc)
            
            # Check if expired
            if expires_dt < now_utc:
                # This is a good place to also downgrade the user in the DB
                db.update_user_subscription(user_id, tier='FREE', expires=None)
                message = (
                    "⚠️ Your subscription has expired. You are now on the FREE tier.\n\n"
                    "Use the /pay command to renew your subscription and regain access to premium features."
                )
                await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
                return

            days_remaining = (expires_dt - now_utc).days
            expiry_date_formatted = expires_dt.strftime('%d %b %Y')
            
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
            pass # Will proceed to show generic upgrade message

    # For FREE users or if something went wrong with date parsing
    subscribe_info = '''
🌟 **LunessaSignals Subscription Tiers** 🌟

Unlock the full power of LunessaSignals with our premium tiers!

| Tier        | Features Included                              | Duration     |
|-------------|--------------------------------------------------|--------------|
| Free        | Basic signals, manual trading only              | Unlimited    |
| Gold        | Premium signals, auto-trade access, priority    | 30 days      |
| Platinum    | All Gold features + early access to new tools   | 90 days      |

---

To upgrade, use the `/pay` command to see payment methods. After payment, the admin will verify it and activate your new tier.
    '''
    await update.message.reply_text(subscribe_info, parse_mode=ParseMode.MARKDOWN)

async def linkbinance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("This is a placeholder for the linkbinance command.")

async def learn_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("This is a placeholder for the learn command.")


async def activate_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.warning("activate_user_command is not yet implemented.")

async def setapi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.warning("setapi_command is not yet implemented.")


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
        logger.info(f"Successfully parsed slip for user {user_id}: {slip_data}")
        slip_data['user_id'] = user_id

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
        logger.error(f"An unexpected error occurred handling slip for user {user_id}: {e}", exc_info=True)
        await update.message.reply_text("An unexpected server error occurred while processing your slip.")


def main() -> None:
    application.add_handler(CommandHandler("redischeck", redis_check_command))
    """Start the bot."""
    db.initialize_database()
    # Run schema migrations to ensure DB is up to date
    db.migrate_schema()

    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("about", trade.about_command))
    application.add_handler(CommandHandler("quest", quest_command)) # This now handles the main trading logic
    application.add_handler(CommandHandler("import", import_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("activate", activate_command))
    application.add_handler(CommandHandler("setapi", set_api_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("close", close_command))
    application.add_handler(CommandHandler("review", review_command))
    application.add_handler(CommandHandler("resonate", resonate_command)) # This now calls the simulation
    application.add_handler(CommandHandler("top_trades", top_trades_command))
    application.add_handler(CommandHandler("referral", referral_command))
    application.add_handler(CommandHandler("leaderboard", leaderboard_command))
    application.add_handler(CommandHandler("myprofile", myprofile_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("subscribe", subscribe_command))
    # Removed duplicate handler registrations for 'setapi' and 'activate'.
    application.add_handler(CommandHandler("broadcast", broadcast_command))
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
    application.add_handler(CommandHandler("autotrade", autotrade_command))
    application.add_handler(CommandHandler("addcoins", addcoins_command))
    application.add_handler(CommandHandler("buy", buy_command))
    application.add_handler(CommandHandler("import_all", import_all_command))
    application.add_handler(CommandHandler("wallet", wallet_command))
    application.add_handler(CommandHandler("checked", checked_command))
    application.add_handler(CommandHandler("autotrade", autotrade_command))
    application.add_handler(CommandHandler("cleanslips", clean_slips_command))

    # Add the slip handler for text messages starting with 'SLIP:'
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r'(?i)^SLIP:'), slip_handler))

    # ---
    # Set up background jobs ---
    job_queue = application.job_queue
    # Schedule the auto-scan job to run every 10 minutes (600 seconds).
    job_queue.run_repeating(trade.scheduled_monitoring_job, interval=config.AI_TRADE_INTERVAL_MINUTES * 60, first=10) # This job now handles all monitoring
    # Schedule the daily summary job to run at 8:00 AM UTC
    job_queue.run_daily(send_daily_status_summary, time=datetime(1, 1, 1, 8, 0, 0, tzinfo=timezone.utc).time())
    job_queue.run_repeating(autotrade_jobs.autotrade_cycle, interval=900, first=10)  # 15 minutes
    job_queue.run_repeating(autotrade_jobs.monitor_autotrades, interval=60, first=10)

    logger.info("Starting bot with market monitor and AI trade monitor jobs scheduled...")
    application.run_polling()

async def clean_slips_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to list and optionally delete Redis trade slips."""
    user_id = update.effective_user.id
    if user_id != config.ADMIN_USER_ID:
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
            key = slip['key']
            data = slip.get('data', {})
            symbol = data.get('symbol', 'N/A')
            timestamp = data.get('timestamp', 'N/A')
            message += f"- `{key}` (Symbol: {symbol}, Time: {timestamp})\n"
        message += "\nTo delete a slip, use: `/cleanslips <full_slip_key>`"
        await update.message.reply_text(message, parse_mode='Markdown')
    else:
        # Delete a specific slip
        slip_key_to_delete = context.args[0]
        try:
            # Ensure the key is bytes as Redis keys are bytes
            if not slip_key_to_delete.startswith("trade:"):
                slip_key_to_delete = "trade:" + slip_key_to_delete
            
            slip_manager.cleanup_slip(slip_key_to_delete.encode())
            await update.message.reply_text(f"✅ Slip `{slip_key_to_delete}` deleted from Redis.")
        except Exception as e:
            logger.error(f"Error deleting slip {slip_key_to_delete}: {e}")
            await update.message.reply_text(f"⚠️ Failed to delete slip `{slip_key_to_delete}`. Error: {e}")

if __name__ == "__main__":
    main()
