
import asyncio
import logging
import json
import functools

import google.generativeai as genai

from . import autotrade_settings as settings_manager
from . import config
from . import db
from . import technical_analyzer
from .core import binance_client, redis_client

logger = logging.getLogger(__name__)

TRADE_MONITOR_INTERVAL_SECONDS = 20

# --- Synchronous Gemini Consultation --- #
def _run_gemini_consultation_sync(api_key: str, model_name: str, prompt: str) -> dict:
    """Synchronous function to be run in a separate thread."""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(prompt, generation_config=genai.types.GenerationConfig(temperature=0.0, response_mime_type="application/json"))
        return json.loads(response.text)
    except Exception as e:
        logger.error(f"[ORACLE_THREAD] Gemini consultation failed in background thread: {e}")
        # In case of a catastrophic failure in the thread, return an empty dict.
        return {}

class TradeExecutor:
    """A unified, high-frequency trading executor."""

    def __init__(self, bot):
        self.bot = bot
        self.user_states = {}

    async def run(self):
        logger.info(f"[EXECUTOR] Starting TradeExecutor run loop (interval: {TRADE_MONITOR_INTERVAL_SECONDS}s)...")
        await self._initial_state_sync()

        while True:
            try:
                user_ids = await db.get_users_with_autotrade_enabled()
                if user_ids:
                    # Process users sequentially to avoid overwhelming resources
                    for user_id in user_ids:
                        await self._process_user(user_id)
            except Exception as e:
                logger.error(f"[EXECUTOR] Unhandled error in main run loop: {e}", exc_info=True)
            
            await asyncio.sleep(TRADE_MONITOR_INTERVAL_SECONDS)

    async def _initial_state_sync(self):
        logger.info("[EXECUTOR_INIT] Performing initial state synchronization from DB to Redis...")
        all_users = await db.get_all_users()
        for user_id in all_users:
            open_trades = await db.get_open_trades_by_user(user_id)
            redis_client.sync_initial_state(user_id, open_trades)
        logger.info("[EXECUTOR_INIT] Initial state sync complete.")

    async def _process_user(self, user_id: int):
        try:
            settings = await settings_manager.get_effective_settings(user_id)
            if not (settings and settings.get('autotrade') == 'on'): return

            await self._check_and_sell_open_trades(user_id, settings)
            await self._analyze_and_conditionally_buy(user_id, settings)

        except Exception as e:
            logger.error(f"[EXECUTOR] Error processing user {user_id}: {e}", exc_info=True)

    async def _check_and_sell_open_trades(self, user_id: int, settings: dict):
        open_trades = await db.get_open_trades_by_user(user_id)
        if open_trades:
            # Sell checks can still be concurrent as they are less memory intensive
            await asyncio.gather(*[self._evaluate_and_execute_sell(dict(trade), settings) for trade in open_trades])

    async def _evaluate_and_execute_sell(self, trade: dict, settings: dict):
        symbol = trade["symbol"]
        current_price = await binance_client.get_current_price(symbol)
        if current_price is None: return

        pnl = ((current_price - trade["buy_price"]) / trade["buy_price"]) * 100
        sell_reason = None

        sl = float(settings.get("stop_loss", 0))
        if sl > 0 and pnl <= -sl: sell_reason = f"🛡️ Stop-loss of {sl}% triggered."

        if not sell_reason:
            sell_reason = self._evaluate_trailing_stop(trade, settings, current_price, pnl)

        if not sell_reason:
            pt = float(settings.get('profit_target', 0))
            if pt > 0 and pnl >= pt: sell_reason = f"🎯 Profit target of {pt}% reached."
        
        if sell_reason:
            await self._sell_trade(trade, current_price, sell_reason)

    def _evaluate_trailing_stop(self, trade: dict, settings: dict, price: float, pnl: float) -> str | None:
        uid, sym = trade["user_id"], trade["symbol"]
        state = self.user_states.setdefault(uid, {}).setdefault(sym, {"armed": False, "peak": 0})
        act, drop = float(settings.get('trailing_activation', 0)), float(settings.get('trailing_drop', 0))

        if not (act > 0 and drop > 0): return None
        if not state["armed"] and pnl >= act: state["armed"], state["peak"] = True, price

        if state["armed"]:
            if price > state["peak"]: state["peak"] = price
            if ((state["peak"] - price) / state["peak"]) * 100 >= drop:
                return f"🐉 Dragon strike! Profit of {pnl:.2f}% locked in."
        return None

    async def _analyze_and_conditionally_buy(self, user_id: int, settings: dict):
        if redis_client.is_gemini_cooldown_active(user_id): return

        watchlist = settings.get('watchlist', '').split(',')
        active_trades = redis_client.get_active_trades(user_id)
        symbols_to_evaluate = sorted([s.strip() for s in watchlist if s.strip() and s.strip() not in active_trades])

        if not symbols_to_evaluate: return

        decisions = redis_client.get_gemini_decision_cache(user_id, symbols_to_evaluate)
        
        if decisions is None:
            decisions = await self._get_batch_gemini_decisions(user_id, symbols_to_evaluate, settings)

        for symbol, decision in decisions.items():
            if decision == "BUY":
                await self._buy_trade(user_id, symbol, settings)

    async def _get_batch_gemini_decisions(self, user_id: int, symbols: list[str], settings: dict) -> dict:
        logger.info(f"[ORACLE] Starting sequential Gemini consultation for user {user_id} on {len(symbols)} symbols.")
        api_key = config.get_next_gemini_key()
        if not api_key: return redis_client.cache_gemini_failure(user_id, symbols)

        # --- STRATEGIC REFACTOR: Process symbols sequentially to conserve memory ---
        batch_analysis = {}
        for symbol in symbols:
            try:
                # Process one symbol at a time
                analysis_result = await self._analyze_and_prepare(symbol, settings)
                if analysis_result:
                    batch_analysis[symbol] = analysis_result
                # A small sleep to prevent hitting API rate limits too aggressively
                await asyncio.sleep(1) 
            except Exception as e:
                logger.error(f"Error during sequential analysis of {symbol}: {e}")
        
        if not batch_analysis: return redis_client.cache_gemini_failure(user_id, symbols)

        prompt = f'''User: {user_id}. Profile: {json.dumps(settings)}. Analyze market data and decide BUY or HOLD for each symbol. Respond in valid JSON.\n\nMarket Data:\n{json.dumps(batch_analysis, indent=2)}'''

        loop = asyncio.get_running_loop()
        decisions = await loop.run_in_executor(
            None,
            functools.partial(_run_gemini_consultation_sync, api_key, config.GEMINI_MODEL, prompt)
        )

        if decisions:
            redis_client.set_gemini_decision_cache(user_id, symbols, decisions)
        else:
            logger.warning(f"[ORACLE] Gemini consultation returned empty. Caching as failure.")
            decisions = redis_client.cache_gemini_failure(user_id, symbols)
        
        redis_client.set_gemini_cooldown(user_id)
        return decisions

    async def _analyze_and_prepare(self, symbol: str, settings: dict) -> dict:
        try:
            klines = await binance_client.get_historical_klines(symbol, '15m', 100)
            return technical_analyzer.analyze_symbol(symbol, klines, settings)
        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")
            return {}

    async def _buy_trade(self, user_id: int, symbol: str, settings: dict):
        price = await binance_client.get_current_price(symbol)
        if not price: return

        size = float(settings.get("trade_size_usdt", 15))
        quantity = size / price
        
        try:
            await db.create_trade(user_id, symbol, price, quantity, size)
            redis_client.add_active_trade(user_id, symbol)
            await self._notify_user(user_id, f"✅ Bought {quantity:.4f} {symbol} at ${price:,.4f}.")
        except Exception as e:
            logger.error(f"[EXECUTOR_BUY] DB/Redis failure on BUY for {symbol}: {e}", exc_info=True)

    async def _sell_trade(self, trade: dict, price: float, reason: str):
        uid, sym, tid = trade["user_id"], trade["symbol"], trade["id"]
        try:
            await db.mark_trade_closed(tid, reason)
            redis_client.remove_active_trade(uid, sym)
            if uid in self.user_states and sym in self.user_states[uid]: del self.user_states[uid][sym]
            pnl = ((price - trade['buy_price']) / trade['buy_price']) * 100
            await self._notify_user(uid, f"🔴 Sold {sym} at ${price:,.4f}. (P/L: {pnl:.2f}%)\n{reason}")
        except Exception as e:
            logger.error(f"[EXECUTOR_SELL] DB/Redis failure on SELL for {sym}: {e}", exc_info=True)

    async def _notify_user(self, user_id: int, message: str):
        try: await self.bot.send_message(chat_id=user_id, text=message)
        except Exception as e: logger.error(f"Notify failed for user {user_id}: {e}")
