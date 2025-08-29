import os

# Load Gemini and Mistral API keys from environment variables
gemini_api_keys = [os.getenv("GEMINI_KEY_1"), os.getenv("GEMINI_KEY_2")]
mistral_api_key = os.getenv("MISTRAL_KEY")

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
            model = genai.GenerativeModel('gemini-1.5-flash')
            response = await model.generate_content_async(prompt)
            return response.text.strip()
        except Exception as e:
            error_msg = str(e)
            if (hasattr(e, 'status_code') and e.status_code == 429) or '429' in error_msg or 'quota' in error_msg.lower():
                continue  # Try next Gemini key
            else:
                break  # For other errors, stop trying Gemini
    # If all Gemini keys fail, use Mistral
    if mistral_api_key:
        mistral_url = "https://api.mistral.ai/v1/chat/completions"
        mistral_headers = {
            "Authorization": f"Bearer {mistral_api_key}",
            "Content-Type": "application/json"
        }
        mistral_payload = {
            "model": "mistral-tiny",
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(mistral_url, headers=mistral_headers, json=mistral_payload)
                resp.raise_for_status()
                mistral_response = resp.json()
                return mistral_response['choices'][0]['message']['content'].strip()
        except Exception as me:
            logger.error(f"Error getting Mistral batch suggestion: {me}")
    return None

import logging
from telegram.ext import ContextTypes
from . import trade
from . import slip_manager
from . import config
import google.generativeai as genai
import asyncio
from .modules import db_access as autotrade_db

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
                if None in (rsi, upper_band, sma, lower_band, bb_std, macd, macd_signal, macd_hist, micro_vwap, volume_ratio, mad):
                    logger.info(f"Not enough kline data for {symbol}, skipping.")
                    continue
                metrics[symbol] = {
                    "RSI": rsi,
                    "Bollinger Bands": f"upper={upper_band}, sma={sma}, lower={lower_band}, std={bb_std}",
                    "MACD": f"{macd}, Signal: {macd_signal}, Histogram: {macd_hist}",
                    "Micro-VWAP": micro_vwap,
                    "Bid/Ask Volume Ratio": volume_ratio,
                    "MAD": mad
                }
        except Exception as e:
            logger.error(f"Error gathering metrics for {symbol}: {e}")

    # Build a single prompt for all coins
    prompt = "Analyze the current market for the following coins. For each coin, answer with only 'buy' or 'hold'.\n\n"
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
            parts = line.strip().split(':')
            if len(parts) == 2:
                symbol, decision = parts[0].strip().upper(), parts[1].strip().lower()
                if symbol in metrics and decision in ['buy', 'hold']:
                    suggestions[symbol] = decision
    return suggestions

async def autotrade_cycle(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Starting autotrade cycle...")
    user_id = config.ADMIN_USER_ID

    if not autotrade_db.get_autotrade_status(user_id):
        logger.info("Autotrade is disabled. Skipping cycle.")
        return

    settings = autotrade_db.get_user_effective_settings(user_id)
    trade_size = float(settings.get('TRADE_SIZE_USDT', 5.0))

    # Prioritize a short list of coins for AI analysis
    prioritized_coins = config.AI_MONITOR_COINS[:5]  # Only analyze top 5 coins
    suggestions = await get_trade_suggestions_from_gemini(prioritized_coins)

    for symbol, decision in suggestions.items():
        if decision == 'buy':
            try:
                usdt_balance = trade.get_account_balance(user_id, 'USDT')
                if usdt_balance is None or usdt_balance < trade_size:
                    logger.warning(f"Insufficient balance to autotrade {symbol}. Current USDT: {usdt_balance:.2f}, Required: {trade_size:.2f}. Skipping buy.")
                    continue

                order, entry_price, quantity = trade.place_buy_order(user_id, symbol, trade_size)

                # Log the trade to the database
                settings = autotrade_db.get_user_effective_settings(user_id)
                stop_loss_price = entry_price * (1 - settings['STOP_LOSS_PERCENTAGE'] / 100)
                take_profit_price = entry_price * (1 + settings['PROFIT_TARGET_PERCENTAGE'] / 100)
                autotrade_db.log_trade(
                    user_id=user_id,
                    coin_symbol=symbol,
                    buy_price=entry_price,
                    stop_loss=stop_loss_price,
                    take_profit=take_profit_price,
                    mode='LIVE',
                    quantity=quantity,
                    trade_size_usdt=trade_size
                )

                slip_manager.create_and_store_slip(symbol, 'buy', quantity, entry_price)

                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🤖 Autotrade executed: Bought {quantity:.4f} {symbol} at ${entry_price:.8f}"
                )
            except trade.TradeError as e:
                logger.error(f"Error executing autotrade for {symbol}: {e}")

async def monitor_autotrades(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Monitoring open autotrades...")
    # Only process keys that match the expected trade slip pattern (e.g., start with 'trade:')
    encrypted_slips = [k for k in slip_manager.redis_client.keys('*') if k.startswith(b'trade:')]

    for encrypted_slip in encrypted_slips:
        try:
                slip = slip_manager.get_and_decrypt_slip(encrypted_slip)
                logger.info(f"Processing slip from key {encrypted_slip}: {slip}")
                if slip is not None and all(k in slip for k in ('symbol', 'price', 'amount')):
                    current_price = trade.get_current_price(slip['symbol'])
                    if not current_price:
                        continue

                    pnl_percent = ((current_price - slip['price']) / slip['price']) * 100

                    if pnl_percent >= autotrade_db.get_user_effective_settings(config.ADMIN_USER_ID)['PROFIT_TARGET_PERCENTAGE']:
                        try:
                            trade.place_sell_order(config.ADMIN_USER_ID, slip['symbol'], slip['amount'])
                            slip_manager.delete_slip(encrypted_slip)
                            await context.bot.send_message(
                                chat_id=config.ADMIN_USER_ID,
                                text=f"🤖 Autotrade closed: Sold {slip['amount']:.4f} {slip['symbol']} at ${current_price:.8f} for a {pnl_percent:.2f}% gain."
                            )
                        except Exception as trade_exc:
                            logger.error(f"Error placing sell order for {slip['symbol']}: {trade_exc}")
                else:
                    # Silently skip invalid or malformed slips
                    continue
        except Exception as e:
            import traceback
            logger.error(f"Error monitoring autotrade: {e}\n{traceback.format_exc()}")
