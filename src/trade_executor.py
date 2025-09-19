
import asyncio
import logging
import time
import json
from datetime import datetime, timezone

import google.generativeai as genai

from . import autotrade_settings as settings_manager
from . import config
from . import db
from . import technical_analyzer
from .core import binance_client

logger = logging.getLogger(__name__)


class TradeExecutor:
    """The main engine for the autotrader, executing the grand strategy."""

    def __init__(self, bot):
        self.bot = bot
        self.user_states = {}

    async def run(self):
        """The main autotrade loop."""
        logger.info("[EXECUTOR] Starting TradeExecutor run loop...")
        if config.GEMINI_API_KEY:
            genai.configure(api_key=config.GEMINI_API_KEY)
            logger.info("[EXECUTOR] Gemini configured successfully.")

        while True:
            logger.debug("[EXECUTOR] Starting new autotrade cycle.")
            try:
                user_ids = db.get_users_with_autotrade_enabled()
                if user_ids:
                    logger.info(f"[EXECUTOR] Processing {len(user_ids)} users with autotrade enabled: {user_ids}")
                    await asyncio.gather(*[self._process_user(user_id) for user_id in user_ids])
                else:
                    logger.debug("[EXECUTOR] No users with autotrade enabled found in this cycle.")
            except Exception as e:
                logger.error(
                    f"[EXECUTOR] Unhandled error in main run loop: {e}", exc_info=True
                )
            
            logger.debug(f"[EXECUTOR] Main loop sleeping for 10 seconds...")
            await asyncio.sleep(10)

    async def _process_user(self, user_id: int):
        """Process all trading logic for a single user."""
        logger.info(f"[EXECUTOR] Starting processing for user_id: {user_id}")
        try:
            settings = await settings_manager.get_effective_settings(user_id)
            if not (settings and settings.get('autotrade') == 'on'):
                logger.debug(f"[EXECUTOR] Autotrade disabled for user {user_id}. Skipping.")
                return

            logger.debug(f"[EXECUTOR] User {user_id} settings loaded: {settings}")

            # 1. Check Grand Campaign Goal
            portfolio_target = float(settings.get("portfolio_target_usdt", 0))
            if portfolio_target > 0:
                logger.debug(f"[EXECUTOR] Checking campaign goal for user {user_id}.")
                current_value = await self._get_total_portfolio_value(user_id, settings)
                if current_value >= portfolio_target:
                    logger.info(f"[EXECUTOR] User {user_id} reached campaign goal. Disabling autotrade.")
                    await self._notify_user(
                        user_id,
                        f"🏆 Grand Campaign Goal of ${portfolio_target:,.2f} USDT reached! Autotrading is now disabled.",
                    )
                    await settings_manager.validate_and_set(user_id, "autotrade", "off")
                    return

            # 2. Manage Open Trades (Sell Logic)
            logger.info(f"[EXECUTOR] Checking open trades for user {user_id}.")
            await self._check_and_sell_open_trades(user_id, settings)
            logger.info(f"[EXECUTOR] Finished checking open trades for user {user_id}.")

            # 3. Look For New Trades (Buy Logic)
            logger.info(f"[EXECUTOR] Looking for new trades for user {user_id}.")
            await self._check_and_open_new_trades(user_id, settings)
            logger.info(f"[EXECUTOR] Finished looking for new trades for user {user_id}.")

        except binance_client.TradeError as e:
            logger.error(f"[EXECUTOR] A Binance API error occurred while processing user {user_id}: {e}")
            await self._notify_user(user_id, f"⚠️ Binance API Error: {e}. Please check your keys and permissions.")
        except Exception as e:
            logger.error(f"[EXECUTOR] An unexpected error occurred while processing user {user_id}: {e}", exc_info=True)
        
        logger.info(f"[EXECUTOR] Finished processing for user_id: {user_id}")


    async def _check_and_sell_open_trades(self, user_id: int, settings: dict):
        open_trades = db.get_open_trades_by_user(user_id)
        logger.debug(f"[EXECUTOR] Found {len(open_trades)} open trades for user {user_id}.")
        if open_trades:
            await asyncio.gather(*[self._evaluate_and_execute_sell(dict(trade), settings) for trade in open_trades])

    async def _evaluate_and_execute_sell(self, trade: dict, settings: dict):
        symbol = trade["symbol"]
        user_id = trade["user_id"]
        logger.info(f"[SELL_EVAL] Evaluating sell for user {user_id}, symbol {symbol}, trade ID {trade['id']}.")
        
        current_price = await binance_client.get_current_price(symbol)
        if current_price is None:
            logger.warning(f"[SELL_EVAL] Could not get current price for {symbol}, skipping evaluation.")
            return

        pnl_percent = ((current_price - trade["buy_price"]) / trade["buy_price"]) * 100
        logger.debug(f"[SELL_EVAL] {symbol}: Current Price=${current_price}, Buy Price=${trade['buy_price']}, PnL={pnl_percent:.2f}%")

        sell_reason = None

        # 1. Check Stop Loss
        stop_loss_percent = float(settings.get("stop_loss", 0))
        if stop_loss_percent > 0 and pnl_percent <= -stop_loss_percent:
            sell_reason = f"🛡️ Stop-loss of {stop_loss_percent}% triggered."
            logger.info(f"[SELL_EVAL] {symbol} triggered stop-loss.")

        # 2. Check Trailing Stop
        if not sell_reason:
            logger.debug(f"[SELL_EVAL] {symbol} checking trailing stop.")
            sell_reason = await self._evaluate_trailing_stop(trade, settings, current_price, pnl_percent)

        # 3. Check Profit Target
        if not sell_reason:
            profit_target = float(settings.get('profit_target', 0))
            if profit_target > 0 and pnl_percent >= profit_target:
                sell_reason = f"🎯 Standard profit target of {profit_target}% reached."
                logger.info(f"[SELL_EVAL] {symbol} reached profit target.")
        
        if sell_reason:
            logger.info(f"[SELL_EVAL] Decision for {symbol}: SELL. Reason: {sell_reason}")
            await self._sell_trade(trade, current_price, sell_reason)
        else:
            logger.debug(f"[SELL_EVAL] Decision for {symbol}: HOLD.")


    async def _evaluate_trailing_stop(self, trade: dict, settings: dict, current_price: float, pnl_percent: float) -> str | None:
        user_id = trade["user_id"]
        symbol = trade["symbol"]
        state = self.user_states.setdefault(user_id, {}).setdefault(symbol, {"trailing_armed": False, "peak_price": 0})

        trailing_activation = float(settings.get('trailing_activation', 0))
        trailing_drop = float(settings.get('trailing_drop', 0))

        if trailing_activation <= 0 or trailing_drop <= 0:
            return None

        if not state["trailing_armed"] and pnl_percent >= trailing_activation:
            state["trailing_armed"] = True
            state["peak_price"] = current_price
            logger.info(f"[TRAILING_STOP] ARMED for {symbol} at initial profit of {pnl_percent:.2f}%")
            await self._notify_user(user_id, f"🐉 Dragon armed for {symbol} at {pnl_percent:.2f}%. Watching for the peak...")

        if state["trailing_armed"]:
            if current_price > state["peak_price"]:
                logger.debug(f"[TRAILING_STOP] {symbol} new peak price: {current_price}")
                state["peak_price"] = current_price

            drop_from_peak_percent = ((state["peak_price"] - current_price) / state["peak_price"]) * 100
            logger.debug(f"[TRAILING_STOP] {symbol} current drop from peak: {drop_from_peak_percent:.2f}% (Target: {trailing_drop}%)")
            
            if drop_from_peak_percent >= trailing_drop:
                final_pnl = ((current_price - trade['buy_price']) / trade['buy_price']) * 100
                logger.info(f"[TRAILING_STOP] TRIGGERED for {symbol}.")
                return f"🐉 Dragon strike! Profit of {final_pnl:.2f}% locked in."
        return None

    async def _check_and_open_new_trades(self, user_id: int, settings: dict):
        watchlist_str = settings.get('watchlist', '')
        if not watchlist_str:
            logger.debug(f"[BUY_EVAL] User {user_id} has an empty watchlist. Skipping new trade check.")
            return

        watchlist = watchlist_str.split(',')
        open_trade_symbols = {t['symbol'] for t in db.get_open_trades_by_user(user_id)}
        logger.debug(f"[BUY_EVAL] User {user_id}: Watchlist: {watchlist}, Open Trades: {open_trade_symbols}")

        for symbol in watchlist:
            symbol = symbol.strip()
            if symbol in open_trade_symbols:
                logger.debug(f"[BUY_EVAL] User {user_id} already has an open trade for {symbol}. Skipping.")
                continue

            logger.info(f"[BUY_EVAL] Evaluating new trade for {symbol} for user {user_id}.")
            decision = await self._get_gemini_buy_decision(symbol)
            
            if decision == "BUY":
                logger.info(f"[BUY_EVAL] Gemini decided BUY for {symbol}. Executing trade.")
                await self._buy_trade(user_id, symbol, settings)
            else:
                logger.info(f"[BUY_EVAL] Gemini decided HOLD for {symbol}.")
                
            logger.debug("[BUY_EVAL] Pausing for 5 seconds to respect API rate limits.")
            await asyncio.sleep(5)

    async def _get_gemini_buy_decision(self, symbol: str) -> str:
        logger.info(f"[GEMINI] Consulting Gemini for buy decision on {symbol}.")
        try:
            market_analysis = {}
            timeframes = ["15m", "1h", "4h"]
            
            klines_tasks = [binance_client.get_historical_klines(symbol=symbol, interval=tf, limit=100) for tf in timeframes]
            results = await asyncio.gather(*klines_tasks, return_exceptions=True)

            for i, result in enumerate(results):
                tf = timeframes[i]
                if isinstance(result, Exception):
                    logger.warning(f"[GEMINI] Could not fetch klines for {symbol} on {tf}: {result}")
                    continue
                if result:
                    logger.debug(f"[GEMINI] Analyzing {symbol} on {tf} timeframe.")
                    market_analysis[tf] = technical_analyzer.analyze_symbol(symbol, result)

            if not market_analysis:
                logger.warning(f"[GEMINI] No market analysis could be generated for {symbol}. Defaulting to HOLD.")
                return "HOLD"

            prompt = (
                f"You are an expert crypto trading analyst.\n"
                f"Analyze the following market data for {symbol} and decide if it is a good time to BUY now or if I should HOLD.\n"
                f"The data includes multiple timeframes with indicators like RSI, MACD, Bollinger Bands, etc.\n"
f"Focus on identifying a strong buy signal. Look for bullish trends, momentum, and potential breakouts. A low RSI is good, but must be confirmed by other indicators.\n"
                f"Respond with only one word: BUY or HOLD.\n\n"
                f"Market Analysis:\n{json.dumps(market_analysis, indent=2)}"
            )
            logger.debug(f"[GEMINI] Prompt for {symbol}:\n{prompt}")

            model = genai.GenerativeModel(config.GEMINI_MODEL)
            response = await model.generate_content_async(prompt, generation_config=genai.types.GenerationConfig(temperature=0.0))

            decision = response.text.strip().upper()
            logger.info(f"[GEMINI] Decision for {symbol}: {decision}")

            if decision not in ["BUY", "HOLD"]:
                logger.warning(f"[GEMINI] Invalid decision '{decision}' for {symbol}. Defaulting to HOLD.")
                return "HOLD"
            return decision

        except Exception as e:
            logger.error(f"[GEMINI] Error consulting Gemini for {symbol}: {e}", exc_info=True)
            return "HOLD"

    async def _buy_trade(self, user_id: int, symbol: str, settings: dict):
        logger.info(f"[TRADE_EXEC] Executing BUY for {symbol} for user {user_id}.")
        price = await binance_client.get_current_price(symbol)
        if price is None:
            logger.error(f"[TRADE_EXEC] Could not get price for {symbol} to execute buy.")
            return

        trade_size_usdt = float(settings.get("trade_size_usdt", 15))
        quantity = trade_size_usdt / price
        logger.info(f"[TRADE_EXEC] BUY {quantity:.8f} {symbol} at ${price:,.4f} for user {user_id}.")
        
        try:
            db.create_trade(user_id=user_id, symbol=symbol, buy_price=price, quantity=quantity, trade_size_usdt=trade_size_usdt)
            await self._notify_user(user_id, f"✅ Bought {quantity:.4f} {symbol} at ${price:,.4f}.")
        except Exception as e:
            logger.error(f"[TRADE_EXEC] Failed to create buy trade for {symbol} in DB: {e}", exc_info=True)
            await self._notify_user(user_id, f"❌ Failed to buy {symbol}. Reason: {e}")

    async def _sell_trade(self, trade: dict, price: float, reason: str):
        user_id = trade["user_id"]
        symbol = trade["symbol"]
        logger.info(f"[TRADE_EXEC] Executing SELL for {symbol} for user {user_id}. Reason: {reason}")
        
        try:
            db.mark_trade_closed(trade["id"], reason=reason)
            
            if user_id in self.user_states and symbol in self.user_states[user_id]:
                del self.user_states[user_id][symbol]
            
            pnl = ((price - trade['buy_price']) / trade['buy_price']) * 100
            await self._notify_user(user_id, f"🔴 Sold {symbol} at ${price:,.4f}. (P/L: {pnl:.2f}%)\n{reason}")
        except Exception as e:
            logger.error(f"[TRADE_EXEC] Failed to execute sell for {symbol}: {e}", exc_info=True)
            await self._notify_user(user_id, f"❌ Failed to sell {symbol}. Reason: {e}")

    async def _get_total_portfolio_value(self, user_id: int, settings: dict) -> float:
        logger.debug(f"[PORTFOLIO] Getting total portfolio value for user {user_id}.")
        try:
            if settings.get("trading_mode", "PAPER") == "PAPER":
                _, paper_balance = db.get_user_trading_mode_and_balance(user_id)
                logger.debug(f"[PORTFOLIO] User {user_id} is in PAPER mode. Balance: {paper_balance}")
                return float(paper_balance)
            else:
                logger.debug(f"[PORTFOLIO] User {user_id} is in LIVE mode. Fetching from Binance.")
                balance = await binance_client.get_total_account_balance_usdt(user_id)
                logger.debug(f"[PORTFOLIO] User {user_id} LIVE balance: {balance}")
                return balance
        except binance_client.TradeError as e:
            logger.error(f"[PORTFOLIO] TradeError getting portfolio value for user {user_id}: {e}")
            raise
        except Exception as e:
            logger.error(f"[PORTFOLIO] Failed to get portfolio value for user {user_id}: {e}", exc_info=True)
            return 0.0

    async def _notify_user(self, user_id: int, message: str):
        logger.debug(f"Notifying user {user_id}: '{message[:50]}...'")
        try:
            await self.bot.send_message(chat_id=user_id, text=message)
        except Exception as e:
            logger.error(f"Failed to send notification to user {user_id}: {e}")
