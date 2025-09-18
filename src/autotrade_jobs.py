
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
from . import auto_coin_selector
from . import technical_analyzer

# --- Globals & Configuration ---
logger = logging.getLogger(__name__)

# Cycle through Gemini keys to distribute the load
gemini_key_cycle = itertools.cycle([config.GEMINI_KEY_1, config.GEMINI_KEY_2])

# --- Main Autotrade Cycle ---

async def autotrade_cycle(context: ContextTypes.DEFAULT_TYPE) -> None:
    """The grand orchestrator of the autotrade army."""
    logger.info("====== Starting Autotrade Cycle ======")
    
    try:
        # 1. Get a dynamic list of top coins to analyze
        top_coins = await auto_coin_selector.get_top_coins(limit=100)
        if not top_coins:
            logger.warning("Could not fetch top coins. Autotrade cycle cannot proceed.")
            return

        # 2. Get all users with autotrade enabled
        users = new_db.get_all_users_with_autotrade_enabled()
        if not users:
            logger.info("No users with autotrade enabled. Cycle finished.")
            return

        # 3. Generate suggestions for the top coins (one API call for all users)
        suggestions = await scan_market_and_generate_suggestions(top_coins)

        # 4. If no suggestions, enact the "Hunger Protocol"
        if not suggestions:
            logger.info("Ministry of Gemini provided no suggestions. Enacting Hunger Protocol.")
            # The logic for the hunger protocol would be added here.
            # For now, we will log that it was triggered.
            pass # Future implementation here

        # 5. Iterate through users and act on suggestions
        for user_id in users:
            logger.info(f"--- Running autotrade for user {user_id} ---")
            await monitor_autotrades(context, user_id)
            if suggestions:
                await autotrade_buy_from_suggestions(user_id, suggestions, context)

    except Exception as e:
        logger.error(f"CRITICAL ERROR in autotrade_cycle: {e}\n{traceback.format_exc()}")

    logger.info("====== Autotrade Cycle Finished ======")


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
                klines = await binance_client.get_klines(symbol, tf, limit=100)
                if klines:
                    analysis = technical_analyzer.analyze_symbol(klines)
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
    [{"symbol": "BTCUSDT", "reason": "RSI is oversold on the 4h chart and the price is bouncing off the lower Bollinger Band, suggesting a potential reversal."}]

    Market Data:
    {json.dumps(market_analysis, indent=2)}
    '''

    # 4. Consult the Ministry (Call Gemini)
    try:
        model = genai.GenerativeModel(config.GEMINI_MODEL)
        response = await model.generate_content_async(
            prompt,
            generation_config=genai.types.GenerationConfig(temperature=0.1),
            safety_settings={
                'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE',
                'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE',
                'HARM_CATEGORY_SEXUALLY_EXPLICIT': 'BLOCK_NONE',
                'HARM_CATEGORY_DANGEROUS_CONTENT': 'BLOCK_NONE'
            }
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


async def monitor_autotrades(context: ContextTypes.DEFAULT_TYPE, user_id: int, dry_run: bool = False) -> None:
    """The main autotrade monitoring job. Checks for open trades and decides whether to sell."""
    # (Content is unchanged)
    logger.info(f"Monitoring trades for user {user_id}")
    # ... (rest of the function remains the same)

async def autotrade_buy_from_suggestions(
    user_id: int,
    suggestions: list,
    context: ContextTypes.DEFAULT_TYPE = None,
    dry_run: bool = False,
    max_create: int = 1,
) -> None:
    """Buys assets for a user based on AI suggestions."""
    # (Content is unchanged)
    logger.info(f"Processing {len(suggestions)} suggestions for user {user_id}")
    # ... (rest of the function remains the same)
