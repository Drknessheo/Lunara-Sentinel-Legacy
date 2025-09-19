
import asyncio
import logging
import json

import google.generativeai as genai

from . import autotrade_settings as settings_manager
from . import config
from . import db
from . import technical_analyzer
from .core import binance_client, redis_client

logger = logging.getLogger(__name__)


class TradeExecutor:
    """The main engine for the autotrader, redesigned for Redis-based state management."""

    def __init__(self, bot):
        self.bot = bot
        self.user_states = {} # For trailing stop state, not for active trades

    async def run(self):
        logger.info("[EXECUTOR] Starting TradeExecutor run loop...")
        if config.GEMINI_API_KEY:
            genai.configure(api_key=config.GEMINI_API_KEY)
            logger.info("[EXECUTOR] Gemini configured successfully.")
        
        # On startup, sync the state for all known users from DB to Redis
        await self._initial_state_sync()

        while True:
            try:
                user_ids = db.get_users_with_autotrade_enabled()
                if user_ids:
                    logger.info(f"[EXECUTOR] Processing {len(user_ids)} users with autotrade enabled: {user_ids}")
                    await asyncio.gather(*[self._process_user(user_id) for user_id in user_ids])
                else:
                    logger.debug("[EXECUTOR] No autotrade users this cycle.")
            except Exception as e:
                logger.error(f"[EXECUTOR] Unhandled error in main run loop: {e}", exc_info=True)
            
            await asyncio.sleep(60)

    async def _initial_state_sync(self):
        logger.info("[EXECUTOR_INIT] Performing initial state synchronization from DB to Redis...")
        all_users = db.get_all_users()
        for user_id in all_users:
            try:
                open_trades = db.get_open_trades_by_user(user_id)
                redis_client.sync_initial_state(user_id, open_trades)
            except Exception as e:
                logger.error(f"[EXECUTOR_INIT] Failed to sync state for user {user_id}: {e}")
        logger.info("[EXECUTOR_INIT] Initial state sync complete.")

    async def _process_user(self, user_id: int):
        logger.info(f"[EXECUTOR] Processing user {user_id}.")
        try:
            settings = await settings_manager.get_effective_settings(user_id)
            if not (settings and settings.get('autotrade') == 'on'):
                return

            await self._check_and_sell_open_trades(user_id, settings)
            await self._check_and_open_new_trades(user_id, settings)

        except Exception as e:
            logger.error(f"[EXECUTOR] Error processing user {user_id}: {e}", exc_info=True)

    async def _check_and_sell_open_trades(self, user_id: int, settings: dict):
        open_trades = db.get_open_trades_by_user(user_id) # Still need details from DB
        if open_trades:
            await asyncio.gather(*[self._evaluate_and_execute_sell(dict(trade), settings) for trade in open_trades])

    async def _evaluate_and_execute_sell(self, trade: dict, settings: dict):
        symbol, user_id = trade["symbol"], trade["user_id"]
        current_price = await binance_client.get_current_price(symbol)
        if current_price is None: return

        pnl = ((current_price - trade["buy_price"]) / trade["buy_price"]) * 100
        sell_reason = None

        sl = float(settings.get("stop_loss", 0))
        if sl > 0 and pnl <= -sl:
            sell_reason = f"🛡️ Stop-loss of {sl}% triggered."

        if not sell_reason:
            sell_reason = await self._evaluate_trailing_stop(trade, settings, current_price, pnl)

        if not sell_reason:
            pt = float(settings.get('profit_target', 0))
            if pt > 0 and pnl >= pt:
                sell_reason = f"🎯 Profit target of {pt}% reached."
        
        if sell_reason:
            await self._sell_trade(trade, current_price, sell_reason)

    async def _evaluate_trailing_stop(self, trade: dict, settings: dict, price: float, pnl: float) -> str | None:
        uid, sym = trade["user_id"], trade["symbol"]
        state = self.user_states.setdefault(uid, {}).setdefault(sym, {"armed": False, "peak": 0})
        act, drop = float(settings.get('trailing_activation', 0)), float(settings.get('trailing_drop', 0))

        if not (act > 0 and drop > 0): return None

        if not state["armed"] and pnl >= act:
            state["armed"], state["peak"] = True, price
            await self._notify_user(uid, f"🐉 Dragon armed for {sym} at {pnl:.2f}%.")

        if state["armed"]:
            if price > state["peak"]: state["peak"] = price
            if ((state["peak"] - price) / state["peak"]) * 100 >= drop:
                return f"🐉 Dragon strike! Profit of {((price - trade['buy_price']) / trade['buy_price']) * 100:.2f}% locked in."
        return None

    async def _check_and_open_new_trades(self, user_id: int, settings: dict):
        watchlist = settings.get('watchlist', '').split(',')
        if not watchlist: return

        active_trade_symbols = redis_client.get_active_trades(user_id)
        symbols_to_evaluate = [s.strip() for s in watchlist if s.strip() and s.strip() not in active_trade_symbols]

        if not symbols_to_evaluate: return

        decisions = await self._get_batch_gemini_decisions(user_id, symbols_to_evaluate, settings)
        for symbol, decision in decisions.items():
            if decision == "BUY":
                await self._buy_trade(user_id, symbol, settings)

    async def _get_batch_gemini_decisions(self, user_id: int, symbols: list[str], settings: dict) -> dict:
        batch_analysis = {}
        tasks = [self._analyze_and_prepare(s, settings) for s in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, dict) and result:
                batch_analysis[symbols[i]] = result
        
        if not batch_analysis: return {}

        prompt = (
            f'''You are an expert crypto trading analyst. The user (ID: {user_id}) has the following risk profile and preferences: {json.dumps(settings, indent=2)}

Analyze the following batch of market data, which includes raw indicators and pre-analyzed "symptoms" based on the user's settings. Your primary goal is to identify strong BUY opportunities that align with the user's settings.

For EACH symbol, decide if it is a strong BUY or HOLD. A "BUY" decision should only be made if there is a compelling, evidence-based reason. Your response MUST be a valid JSON object with each symbol as a key and its decision ("BUY" or "HOLD") as the value.

Example: {{"BTCUSDT": "BUY", "ETHUSDT": "HOLD"}}

Market Data with Symptoms:
{json.dumps(batch_analysis, indent=2)}'''
        )

        try:
            model = genai.GenerativeModel(config.GEMINI_MODEL)
            response = await model.generate_content_async(prompt, generation_config=genai.types.GenerationConfig(temperature=0.0, response_mime_type="application/json"))
            return json.loads(response.text)
        except Exception as e:
            logger.error(f"[GEMINI_BATCH] Error: {e}")
            return {s: "HOLD" for s in symbols}

    async def _analyze_and_prepare(self, symbol: str, settings: dict) -> dict:
        """Helper to run analysis for a single symbol and handle potential errors."""
        try:
            klines = await binance_client.get_historical_klines(symbol, '15m', 100)
            return technical_analyzer.analyze_symbol(symbol, klines, settings)
        except Exception as e:
            logger.error(f"Error analyzing {symbol} for batch decisions: {e}")
            return {}

    async def _buy_trade(self, user_id: int, symbol: str, settings: dict):
        price = await binance_client.get_current_price(symbol)
        if not price: return

        size = float(settings.get("trade_size_usdt", 15))
        quantity = size / price
        
        try:
            db.create_trade(user_id, symbol, price, quantity, size)
            redis_client.add_active_trade(user_id, symbol)
            await self._notify_user(user_id, f"✅ Bought {quantity:.4f} {symbol} at ${price:,.4f}.")
        except Exception as e:
            logger.error(f"[TRADE_EXEC] DB/Redis failure on BUY for {symbol}: {e}")

    async def _sell_trade(self, trade: dict, price: float, reason: str):
        uid, sym, tid = trade["user_id"], trade["symbol"], trade["id"]
        try:
            db.mark_trade_closed(tid, reason)
            redis_client.remove_active_trade(uid, sym)
            if uid in self.user_states and sym in self.user_states[uid]: del self.user_states[uid][sym]
            pnl = ((price - trade['buy_price']) / trade['buy_price']) * 100
            await self._notify_user(uid, f"🔴 Sold {sym} at ${price:,.4f}. (P/L: {pnl:.2f}%)\n{reason}")
        except Exception as e:
            logger.error(f"[TRADE_EXEC] DB/Redis failure on SELL for {sym}: {e}")

    async def _notify_user(self, user_id: int, message: str):
        try: await self.bot.send_message(chat_id=user_id, text=message)
        except Exception as e: logger.error(f"Notify failed for user {user_id}: {e}")
