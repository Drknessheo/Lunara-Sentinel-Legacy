
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
        if config.GEMINI_API_KEY:
            genai.configure(api_key=config.GEMINI_API_KEY)

        while True:
            try:
                user_ids = db.get_users_with_autotrade_enabled()
                await asyncio.gather(*[self._process_user(user_id) for user_id in user_ids])
            except Exception as e:
                logger.error(
                    f"[AUTOTRADE] Unhandled error in main loop: {e}", exc_info=True
                )
            await asyncio.sleep(10)  # Main loop delay

    async def _process_user(self, user_id: int):
        """Process all trading logic for a single user."""
        try:
            settings = await settings_manager.get_effective_settings(user_id)
            if not (settings and settings.get('autotrade') == 'on'):
                return # Autotrade is not enabled or settings are missing

            # 1. Check Grand Campaign Goal
            portfolio_target = float(settings.get("portfolio_target_usdt", 0))
            if portfolio_target > 0:
                current_value = await self._get_total_portfolio_value(user_id, settings)
                if current_value >= portfolio_target:
                    await self._notify_user(
                        user_id,
                        f"🏆 Grand Campaign Goal of ${portfolio_target:,.2f} USDT reached! Autotrading is now disabled.",
                    )
                    await settings_manager.validate_and_set(user_id, "autotrade", "off")
                    return

            # 2. Manage Open Trades (Sell Logic)
            await self._check_and_sell_open_trades(user_id, settings)

            # 3. Look For New Trades (Buy Logic)
            await self._check_and_open_new_trades(user_id, settings)

        except binance_client.TradeError as e:
            logger.error(f"A Binance API error occurred while processing user {user_id}: {e}")
            await self._notify_user(user_id, f"⚠️ Binance API Error: {e}. Please check your keys and permissions.")
            # Optionally disable autotrade for this user
            # await settings_manager.validate_and_set(user_id, "autotrade", "off")
        except Exception as e:
            logger.error(f"An unexpected error occurred while processing user {user_id}: {e}", exc_info=True)


    async def _check_and_sell_open_trades(self, user_id: int, settings: dict):
        open_trades = db.get_open_trades_by_user(user_id)
        await asyncio.gather(*[self._evaluate_and_execute_sell(dict(trade), settings) for trade in open_trades])

    async def _evaluate_and_execute_sell(self, trade: dict, settings: dict):
        symbol = trade["symbol"]
        current_price = await binance_client.get_current_price(symbol)
        if current_price is None:
            logger.warning(
                f"Could not get current price for {symbol}, skipping evaluation."
            )
            return

        pnl_percent = ((current_price - trade["buy_price"]) / trade["buy_price"]) * 100

        # Reason priority: Stop-loss -> Max Hold Time -> Trailing Stop -> Profit Target
        sell_reason = None

        # 1. Check Stop Loss
        stop_loss_percent = float(settings.get("stop_loss", 0))
        if stop_loss_percent > 0 and pnl_percent <= -stop_loss_percent:
            sell_reason = f"🛡️ Stop-loss of {stop_loss_percent}% triggered."

        # 2. Check Trailing Stop (The Dragon)
        if not sell_reason:
            sell_reason = await self._evaluate_trailing_stop(
                trade, settings, current_price, pnl_percent
            )

        # 3. Check Profit Target (if trailing stop not active)
        if not sell_reason:
            profit_target = float(settings.get('profit_target', 0))
            if profit_target > 0 and pnl_percent >= profit_target:
                sell_reason = f"🎯 Standard profit target of {profit_target}% reached."
        
        if sell_reason:
            await self._sell_trade(trade, current_price, sell_reason)

    async def _evaluate_trailing_stop(
        self, trade: dict, settings: dict, current_price: float, pnl_percent: float
    ) -> str | None:
        user_id = trade["user_id"]
        symbol = trade["symbol"]
        state = self.user_states.setdefault(user_id, {}).setdefault(
            symbol, {"trailing_armed": False, "peak_price": 0}
        )

        trailing_activation = float(settings.get('trailing_activation', 0))
        trailing_drop = float(settings.get('trailing_drop', 0))

        if trailing_activation <= 0 or trailing_drop <= 0:
            return None # Trailing stop is not configured

        # Arm the trailing stop if activation profit is hit
        if not state["trailing_armed"] and pnl_percent >= trailing_activation:
            state["trailing_armed"] = True
            state["peak_price"] = current_price
            logger.info(
                f"Trailing stop ARMED for {symbol} at initial profit of {pnl_percent:.2f}%"
            )
            await self._notify_user(
                user_id,
                f"🐉 Dragon armed for {symbol} at {pnl_percent:.2f}%. Watching for the peak...",
            )

        if state["trailing_armed"]:
            if current_price > state["peak_price"]:
                state["peak_price"] = current_price

            drop_from_peak_percent = (
                (state["peak_price"] - current_price) / state["peak_price"]
            ) * 100
            if drop_from_peak_percent >= trailing_drop:
                final_pnl = (
                    (current_price - trade['buy_price']) / trade['buy_price']
                ) * 100
                return f"🐉 Dragon strike! Profit of {final_pnl:.2f}% locked in."
        return None

    async def _check_and_open_new_trades(self, user_id: int, settings: dict):
        watchlist_str = settings.get('watchlist', '')
        if not watchlist_str: return

        watchlist = watchlist_str.split(',')
        open_trade_symbols = {t['symbol'] for t in db.get_open_trades_by_user(user_id)}

        for symbol in watchlist:
            if symbol in open_trade_symbols:
                continue

            decision = await self._get_gemini_buy_decision(symbol)
            if decision == "BUY":
                await self._buy_trade(user_id, symbol, settings)
                
            # IMPERIAL DECREE: We must pause to appease the Gemini API gods.
            # A 5-second delay ensures we do not exceed the 15 requests/minute free tier limit.
            await asyncio.sleep(5)

    async def _get_gemini_buy_decision(self, symbol: str) -> str:
        """Consults Gemini for a buy/hold decision."""
        try:
            market_analysis = {}
            timeframes = ["15m", "1h", "4h"]
            
            klines_tasks = [binance_client.get_historical_klines(symbol=symbol, interval=tf, limit=100) for tf in timeframes]
            results = await asyncio.gather(*klines_tasks, return_exceptions=True)

            for i, result in enumerate(results):
                tf = timeframes[i]
                if isinstance(result, Exception):
                    logger.warning(f"Could not fetch klines for {symbol} on {tf} timeframe: {result}")
                    continue
                if result:
                    market_analysis[tf] = technical_analyzer.analyze_symbol(symbol, result)

            if not market_analysis:
                return "HOLD"

            prompt = (
                f"You are an expert crypto trading analyst.\n"
                f"Analyze the following market data for {symbol} and decide if it is a good time to BUY now or if I should HOLD.\n"
                f"The data includes multiple timeframes with indicators like RSI, MACD, Bollinger Bands, etc.\n"
                f"Focus on identifying a strong buy signal. Look for bullish trends, momentum, and potential breakouts. A low RSI is good, but must be confirmed by other indicators.\n"
                f"Respond with only one word: BUY or HOLD.\n\n"
                f"Market Analysis:\n{json.dumps(market_analysis, indent=2)}"
            )

            model = genai.GenerativeModel(config.GEMINI_MODEL)
            response = await model.generate_content_async(
                prompt,
                generation_config=genai.types.GenerationConfig(temperature=0.0),
            )

            decision = response.text.strip().upper()
            if decision in ["BUY", "HOLD"]:
                logger.info(f"Gemini buy-decision for {symbol}: {decision}")
                return decision
            else:
                logger.warning(
                    f"Gemini gave an invalid buy/hold decision: '{decision}'. Defaulting to HOLD."
                )
                return "HOLD"

        except Exception as e:
            logger.error(f"Error consulting Gemini for buy advice on {symbol}: {e}", exc_info=True)
            return "HOLD"

    async def _buy_trade(self, user_id: int, symbol: str, settings: dict):
        price = await binance_client.get_current_price(symbol)
        if price is None:
            logger.error(f"Could not get price for {symbol} to execute buy.")
            return

        trade_size_usdt = float(settings.get("trade_size_usdt", 15)) # Default to 15 USDT
        quantity = trade_size_usdt / price

        logger.info(f"Executing BUY for {symbol} for user {user_id} at {price}.")
        try:
            # This is where the actual live order would be placed.
            # For now, we are creating a mock entry in the database.
            db.create_trade(
                user_id=user_id,
                symbol=symbol,
                buy_price=price,
                quantity=quantity,
                trade_size_usdt=trade_size_usdt,
            )
            await self._notify_user(
                user_id, f"✅ Bought {quantity:.4f} {symbol} at ${price:,.4f}."
            )
        except Exception as e:
            logger.error(f"Failed to execute buy for {symbol}: {e}", exc_info=True)
            await self._notify_user(user_id, f"❌ Failed to buy {symbol}. Reason: {e}")

    async def _sell_trade(self, trade: dict, price: float, reason: str):
        user_id = trade["user_id"]
        symbol = trade["symbol"]
        logger.info(
            f"Executing SELL for {symbol} for user {user_id} at {price}. Reason: {reason}"
        )
        try:
            # This is where the actual live order would be placed.
            # For now, we just update the database.
            db.mark_trade_closed(trade["id"], reason=reason)
            
            # Reset state for the symbol
            if user_id in self.user_states and symbol in self.user_states[user_id]:
                del self.user_states[user_id][symbol]
            
            pnl = ((price - trade['buy_price']) / trade['buy_price']) * 100
            await self._notify_user(user_id, f"🔴 Sold {symbol} at ${price:,.4f}. (P/L: {pnl:.2f}%)\n{reason}")

        except Exception as e:
            logger.error(f"Failed to execute sell for {symbol}: {e}", exc_info=True)
            await self._notify_user(user_id, f"❌ Failed to sell {symbol}. Reason: {e}")

    async def _get_total_portfolio_value(self, user_id: int, settings: dict) -> float:
        try:
            if settings.get("trading_mode", "PAPER") == "PAPER":
                _, paper_balance = db.get_user_trading_mode_and_balance(user_id)
                return float(paper_balance)
            else:
                # This is now a non-blocking call
                return await binance_client.get_total_account_balance_usdt(user_id)
        except binance_client.TradeError as e:
            logger.error(f"TradeError getting portfolio value for user {user_id}: {e}")
            raise # Re-raise to be handled by the main user processing loop
        except Exception as e:
            logger.error(f"Failed to get portfolio value for user {user_id}: {e}", exc_info=True)
            return 0.0

    async def _notify_user(self, user_id: int, message: str):
        try:
            await self.bot.send_message(chat_id=user_id, text=message)
        except Exception as e:
            logger.error(f"Failed to send notification to user {user_id}: {e}")
