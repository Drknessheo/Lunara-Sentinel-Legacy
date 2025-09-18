
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
from .core import trading_logic, binance_client
from . import gemini_cache
from . import autotrade_settings
from . import db as new_db

# --- Globals & Configuration ---
logger = logging.getLogger(__name__)

# --- Main Autotrade Cycle ---

async def autotrade_cycle(context: ContextTypes.DEFAULT_TYPE) -> None:
    """The grand orchestrator of the autotrade army."""
    logger.info("====== Starting Autotrade Cycle ======")
    
    # 1. Monitor existing trades for selling opportunities
    await monitor_autotrades(context)
    
    # 2. Get AI suggestions and create new trades
    # In a real implementation, you would get suggestions from an AI model.
    # For now, we will use a placeholder.
    # To keep things simple, we'll run this for all users with autotrade enabled.
    users = new_db.get_all_users_with_autotrade_enabled()
    for user_id in users:
        # In a real scenario, you'd get suggestions from your AI model here.
        # For now, we'll just use an empty list.
        suggestions = [] 
        await autotrade_buy_from_suggestions(user_id, suggestions, context)

    logger.info("====== Autotrade Cycle Finished ======")


# --- Core AI and Trade Logic ---

async def monitor_autotrades(context: ContextTypes.DEFAULT_TYPE = None, dry_run: bool = False) -> None:
    """The main autotrade monitoring job. Checks for open trades and decides whether to sell."""
    # ... (existing monitor logic remains the same)

async def autotrade_buy_from_suggestions(
    user_id: int,
    suggestions: list, # Expects a list of dicts with {'symbol': '...', 'reason': '...'}
    context: ContextTypes.DEFAULT_TYPE = None,
    dry_run: bool = False,
    max_create: int = 1,
) -> None:
    """Buys assets for a user based on AI suggestions, with reserve detection."""
    logger.info(f"[BUY] Starting autotrade buy cycle for user {user_id}. Suggestions: {suggestions}")
    
    created_trades = 0
    mode, paper_balance = new_db.get_user_trading_mode_and_balance(user_id)
    settings = autotrade_settings.get_effective_settings(user_id)
    
    # Reserve Detection
    available_balance = 0
    if mode == 'LIVE':
        try:
            balances = await binance_client.get_all_spot_balances(user_id)
            usdt_balance = next((item for item in balances if item["asset"] == "USDT"), None)
            if usdt_balance:
                available_balance = float(usdt_balance['free'])
            else:
                logger.warning(f"User {user_id} is in LIVE mode but has no USDT balance.")
                return
        except binance_client.TradeError as e:
            logger.error(f"Could not check balance for user {user_id}: {e}")
            return
    else: # PAPER mode
        available_balance = paper_balance

    if available_balance <= 0:
        logger.info(f"User {user_id} has no available balance for new trades. Skipping buy cycle.")
        return

    # Determine how much to invest per trade
    investment_per_trade_percent = float(settings.get("INVESTMENT_PER_TRADE_PERCENTAGE", 5.0))
    amount_to_invest = available_balance * (investment_per_trade_percent / 100.0)

    # --- Iterate through suggestions and create trades ---
    for suggestion in suggestions:
        if created_trades >= max_create:
            logger.info(f"Reached max_create limit of {max_create} for this cycle.")
            break

        symbol = suggestion.get('symbol')
        if not symbol:
            continue

        # Check if there is already an open trade for this symbol
        open_trades = new_db.get_open_trades_by_user(user_id)
        if any(trade['symbol'] == symbol for trade in open_trades):
            logger.info(f"Skipping buy for {symbol}, an open trade already exists for user {user_id}.")
            continue
        
        # Check if the investment amount is too small
        if amount_to_invest < 15: # Binance minimum order size is around $10, add a buffer
            logger.warning(f"Investment amount ${amount_to_invest:.2f} is too low to create a trade for {symbol}. Skipping.")
            continue

        if dry_run:
            logger.info(f"[BUY] DRY RUN - Would create a trade for {symbol} for user {user_id} with ${amount_to_invest:.2f}.")
            created_trades += 1
            continue

        try:
            logger.info(f"Attempting to place BUY order for user {user_id} for {symbol} with ${amount_to_invest:.2f}")
            trade_result = await trading_logic.place_buy_order_logic(user_id, symbol, amount_to_invest)

            if trade_result and trade_result.get('success'):
                created_trades += 1
                trade_id = trade_result.get('trade_id')
                msg_text = f"🤖 Autotrade created: Bought {trade_result['quantity']} {symbol} @ ${trade_result['price']:.4f}. (Trade ID: {trade_id})"
                
                # Create a SLIP file for monitoring
                slip_manager.create_slip(f"trade:{trade_id}", {
                    'user_id': user_id,
                    'symbol': symbol,
                    'price': trade_result['price'],
                    'amount': trade_result['quantity'],
                    'sandpaper': True # Mark as an autotrade
                })

                if context and getattr(context, "bot", None):
                    await context.bot.send_message(chat_id=user_id, text=msg_text)
                else:
                    logger.info(msg_text)
            else:
                error_msg = trade_result.get('error', 'Unknown error')
                logger.error(f"Buy order failed for user {user_id} for {symbol}: {error_msg}")

        except Exception as e:
            logger.error(f"Critical error in autotrade_buy_from_suggestions for {symbol}: {e}\n{traceback.format_exc()}")

