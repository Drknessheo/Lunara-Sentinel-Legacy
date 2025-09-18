
# Standard Library Imports
import os
import time
import json
import logging
import traceback
import itertools

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
from .modules import auto_coin_selector
from . import technical_analyzer
from . import risk_management

# --- Globals & Configuration ---
logger = logging.getLogger(__name__)

gemini_key_cycle = itertools.cycle([config.GEMINI_KEY_1, config.GEMINI_KEY_2])

# --- Main Autotrade Cycle ---

async def autotrade_cycle(context: ContextTypes.DEFAULT_TYPE) -> None:
    """The grand orchestrator of the autotrade army."""
    logger.info("====== Starting Autotrade Cycle ======")
    try:
        top_coins = auto_coin_selector.fetch_top_binance_coins(limit=100)
        if not top_coins:
            logger.warning("Could not fetch top coins. Autotrade cycle cannot proceed.")
            return

        users = new_db.get_all_users_with_autotrade_enabled()
        if not users:
            logger.info("No users with autotrade enabled. Cycle finished.")
            return
        
        # Process selling decisions for all users first
        for user_id in users:
            logger.info(f"--- Monitoring trades for user {user_id} ---")
            await monitor_autotrades(context, user_id)

        # Then, process buying decisions
        logger.info("--- Generating global buy suggestions ---")
        suggestions = await scan_market_and_generate_suggestions(top_coins)

        if not suggestions:
            logger.info("Ministry of Gemini provided no buy suggestions. Enacting Hunger Protocol.")
            # Hunger Protocol Logic would go here

        if suggestions:
            for user_id in users:
                logger.info(f"--- Processing buy suggestions for user {user_id} ---")
                await autotrade_buy_from_suggestions(user_id, suggestions, context)

    except Exception as e:
        logger.error(f"CRITICAL ERROR in autotrade_cycle: {e}\
{traceback.format_exc()}")

    logger.info("====== Autotrade Cycle Finished ======")


# --- Sell-side Logic: The Imperial Risk Manager ---

async def monitor_autotrades(context: ContextTypes.DEFAULT_TYPE, user_id: int, dry_run: bool = False) -> None:
    """Checks open trades and uses Gemini to decide whether to sell."""
    open_trades = new_db.get_open_trades_by_user(user_id)
    if not open_trades:
        logger.info(f"No open trades to monitor for user {user_id}")
        return

    for trade in open_trades:
        symbol = trade['symbol']
        try:
            current_price = await binance_client.get_current_price(symbol, user_id=user_id)
            if not current_price:
                logger.warning(f"Could not fetch price for {symbol}. Skipping trade {trade['id']}.")
                continue

            # --- The Ministry's Wisdom: Dynamic Stop-Loss ---
            trade = risk_management.manage_open_trade(trade, current_price)
            new_db.update_trade(trade) # Persist the updated trade
            # ---------------------------------------------------

            pnl_percent = ((current_price - trade['buy_price']) / trade['buy_price']) * 100

            # Consult the Ministry for sell advice
            advice = await get_gemini_sell_advice(symbol, pnl_percent, trade['buy_price'])

            if advice == "SELL":
                logger.info(f"Ministry advises to SELL {symbol} (PnL: {pnl_percent:.2f}%). Attempting to sell.")
                if dry_run:
                    logger.info(f"[DRY RUN] Would sell {trade['quantity']} of {symbol}.")
                    continue

                await execute_sell(trade, current_price, pnl_percent, "ministry_advice", context)
            
            else: # advice is HOLD or something else
                logger.info(f"Ministry advises to HOLD {symbol} (PnL: {pnl_percent:.2f}%). No action taken.")

        except Exception as e:
            logger.error(f"Error processing trade {trade['id']} for {symbol}: {e}\
{traceback.format_exc()}")

async def get_gemini_sell_advice(symbol: str, pnl_percent: float, buy_price: float) -> str:
    """Consults Gemini to get a 'SELL' or 'HOLD' recommendation for an open trade."""
    gemini_key = next(gemini_key_cycle)
    if not gemini_key:
        return "HOLD" # Default to holding if no key
    genai.configure(api_key=gemini_key)

    try:
        # Gather multi-timeframe analysis
        market_analysis = {}
        for tf in ['15m', '1h', '4h']:
            klines = binance_client.get_historical_klines(symbol=symbol, interval=tf, limit=100)
            if klines:
                market_analysis[tf] = technical_analyzer.analyze_symbol(symbol, klines)

        if not market_analysis:
            return "HOLD" # Not enough data to make a decision

        prompt = f"""
        You are a risk management bot for a crypto autotrader.
        I have an open position on {symbol} with a current profit of {pnl_percent:.2f}% (bought at ${buy_price}).
        Analyze the provided market data. Decide if I should SELL NOW to secure profits before a potential downturn, or HOLD for more gains.

        CRITERIA FOR SELLING:
        - Signs of a trend reversal (e.g., RSI dropping from overbought, bearish MACD crossover).
        - Price hitting upper Bollinger Bands and showing weakness.
        - Significant bearish signals on smaller timeframes (15m, 1h) which could indicate an imminent dip.

        CRITERIA FOR HOLDING:
        - Strong upward trend is still intact.
        - Indicators are neutral or still bullish.
        - The profit is small and the trend suggests more upside potential.

        Based on your analysis of the data, your response MUST be a single word: 'SELL' or 'HOLD'.

        Market Data:
        {json.dumps(market_analysis, indent=2)}
        """

        model = genai.GenerativeModel(config.GEMINI_MODEL)
        response = await model.generate_content_async(prompt, generation_config=genai.types.GenerationConfig(temperature=0.0))
        
        decision = response.text.strip().upper()
        if decision in ["SELL", "HOLD"]:
            return decision
        else:
            logger.warning(f"Gemini gave an invalid sell/hold decision: '{decision}'. Defaulting to HOLD.")
            return "HOLD"

    except Exception as e:
        logger.error(f"Error consulting Gemini for sell advice on {symbol}: {e}")
        return "HOLD" # Default to holding on error

async def execute_sell(trade: dict, current_price: float, pnl_percent: float, reason: str, context: ContextTypes.DEFAULT_TYPE):
    """Executes the sell order and notifies the user."""
    user_id = trade['user_id']
    symbol = trade['symbol']
    quantity = trade['quantity']

    try:
        sell_result = await trading_logic.place_sell_order_logic(user_id, symbol, quantity)
        if sell_result and sell_result.get('success'):
            new_db.mark_trade_closed(trade['id'], reason=reason)
            sell_price = sell_result.get('price', current_price)
            msg = f"🤖 Autotrade closed via {reason.replace('_', ' ')}: Sold {quantity} {symbol} @ ${sell_price:.4f}. PnL: {pnl_percent:.2f}%"
            if context and getattr(context, "bot", None):
                await context.bot.send_message(chat_id=user_id, text=msg)
            logger.info(msg)
        else:
            error_msg = sell_result.get('error', 'Unknown error during sell.')
            logger.error(f"Failed to execute sell for {symbol}: {error_msg}")
    except trading_logic.TradeError as e:
        logger.error(f"TradeError on sell for {symbol}: {e}")
    except Exception as e:
        logger.error(f"Critical error during sell execution for {symbol}: {e}\
{traceback.format_exc()}")


# --- Buy-side Logic ---

async def scan_market_and_generate_suggestions(watchlist: list) -> list:
    """Scans the market using Gemini to generate trading suggestions."""
    logger.info(f"Scanning market with Gemini for {len(watchlist)} symbols.")
    if not watchlist:
        return []

    try:
        suggestions = await get_gemini_suggestions(watchlist)
        return suggestions
    except Exception as e:
        logger.error(f"Error getting Gemini suggestions: {e}")
        return []

async def get_gemini_suggestions(watchlist: list) -> list:
    """
    Fetches market data, gets suggestions from Gemini, and returns them.
    """
    # 1. Select the next Gemini API key from the cycle
    gemini_key = next(gemini_key_cycle)
    if not gemini_key:
        logger.error("No Gemini API key available for this cycle.")
        return []
    genai.configure(api_key=gemini_key)

    # 2. Gather comprehensive market data
    market_analysis = {}
    timeframes = ['1h', '4h', '1d']
    for symbol in watchlist:
        if not symbol:
            continue
        try:
            market_analysis[symbol] = {}
            for tf in timeframes:
                klines = binance_client.get_historical_klines(symbol=symbol, interval=tf, limit=100)
                if klines:
                    analysis = technical_analyzer.analyze_symbol(symbol, klines)
                    market_analysis[symbol][tf] = analysis
        except Exception as e:
            logger.warning(f"Could not analyze {symbol}: {e}")

    if not market_analysis:
        logger.info("No market analysis data generated.")
        return []

    # 3. Construct the Grand Prompt for the Gemini Ministry
    prompt = f'''
    You are a master crypto trading analyst. Your task is to identify the single best buying opportunity from the provided market data.
    Analyze the technical indicators (RSI, MACD, Bollinger Bands) across multiple timeframes (1h, 4h, 1d) for each symbol.
    
    A strong buy signal is typically characterized by:
    - RSI in the 1h or 4h timeframe being low (e.g., below 35) but showing signs of recovery.
    - MACD line crossing above the signal line.
    - Price bouncing off the lower Bollinger Band.
    - Consistent signals across multiple timeframes are stronger.

    Conversely, be cautious of:
    - Coins at or near their all-time high (ATH). I will not provide ATH data, but you should infer risk from high prices and strong upward trends.
    - Coins that have experienced a recent, massive pump. Avoid buying at the peak.
    - General bearish market sentiment (e.g., BTC showing weakness).

    Review all the data below and choose the ONE symbol with the highest potential for a profitable entry RIGHT NOW.
    Your response MUST be a valid JSON array containing a single object for the chosen symbol, or an empty array if no symbol meets the criteria.

    Example Response:
    [{{"symbol": "BTCUSDT", "reason": "RSI is oversold on the 4h chart and the price is bouncing off the lower Bollinger Band, suggesting a potential reversal."}}]

    Market Data:
    {json.dumps(market_analysis, indent=2)}
    '''

    # 4. Consult the Ministry (Call Gemini)
    try:
        model = genai.GenerativeModel(config.GEMINI_MODEL)
        response = await model.generate_content_async(
            prompt,
            generation_config=genai.types.GenerationConfig(temperature=0.1),
            safety_settings={{
                'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE',
                'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE',
                'HARM_CATEGORY_SEXUALLY_EXPLICIT': 'BLOCK_NONE',
                'HARM_CATEGORY_DANGEROUS_CONTENT': 'BLOCK_NONE'
            }}
        )
        
        # 5. Parse the Ministry's Decree
        text_response = response.text.strip().replace('`', '').replace('json', '')
        suggestions = json.loads(text_response)
        
        logger.info(f"Ministry of Gemini has spoken: {suggestions}")
        return suggestions if isinstance(suggestions, list) else []

    except Exception as e:
        logger.error(f"Error consulting the Ministry of Gemini: {e}")
        if 'response' in locals():
            logger.error(f"Gemini Raw Response: {response.text}")
        return []


async def autotrade_buy_from_suggestions(
    user_id: int,
    suggestions: list,
    context: ContextTypes.DEFAULT_TYPE = None,
    dry_run: bool = False,
    max_create: int = 1,
) -> None:
    """Buys assets for a user based on AI suggestions, with reserve detection."""
    # ... (rest of the function remains the same)
