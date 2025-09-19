import asyncio
import logging
import time
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
                for user_id in user_ids:
                    await self._process_user(user_id)
            except Exception as e:
                logger.error(
                    f"[AUTOTRADE] Unhandled error in main loop: {e}", exc_info=True
                )
            await asyncio.sleep(10)  # Main loop delay

    async def _process_user(self, user_id: int):
        """Process all trading logic for a single user."""
        settings = settings_manager.get_effective_settings(user_id)
        if not settings.get("autotrade", False):
            return  # Double check autotrade is on

        # 1. Check Grand Campaign Goal
        portfolio_target = settings.get("portfolio_target_usdt", 0)
        if portfolio_target > 0:
            current_value = await self._get_total_portfolio_value(user_id, settings)
            if current_value >= portfolio_target:
                await self._notify_user(
                    user_id,
                    f"🏆 Grand Campaign Goal of ${portfolio_target:,.2f} USDT reached! Autotrading is now disabled.",
                )
                # Turn off autotrade
                settings_manager.validate_and_set(user_id, "autotrade", "off")
                return

        # 2. Manage Open Trades (Sell Logic)
        await self._check_and_sell_open_trades(user_id, settings)

        # 3. Look For New Trades (Buy Logic)
        await self._check_and_open_new_trades(user_id, settings)

    async def _check_and_sell_open_trades(self, user_id: int, settings: dict):
        open_trades = db.get_open_trades_by_user(user_id)
        for trade in open_trades:
            await self._evaluate_and_execute_sell(trade, settings)

    async def _evaluate_and_execute_sell(self, trade: dict, settings: dict):
        symbol = trade["symbol"]
        current_price = binance_client.get_current_price(symbol)
        if current_price is None:
            logger.warning(
                f"Could not get current price for {symbol}, skipping evaluation."
            )
            return

        pnl_percent = ((current_price - trade["buy_price"]) / trade["buy_price"]) * 100

        # Reason priority: Stop-loss -> Max Hold Time -> Trailing Stop
        sell_reason = None

        # 1. Check Stop Loss
        stop_loss_percent = settings["stop_loss_percentage"]
        if pnl_percent <= -stop_loss_percent:
            sell_reason = f"🛡️ Stop-loss of {stop_loss_percent}% triggered."

        # 2. Check Max Hold Time (if no SL triggered)
        if not sell_reason:
            max_hold_time_sec = settings["max_hold_time"]
            trade_age_sec = (
                datetime.now(timezone.utc) - trade["buy_time"]
            ).total_seconds()
            if trade_age_sec > max_hold_time_sec:
                sell_reason = f"⏳ Tactical retreat after {max_hold_time_sec // 3600}h. P/L: {pnl_percent:.2f}%"

        # 3. Check Trailing Stop (The Dragon) (if no other reason)
        if not sell_reason:
            sell_reason = await self._evaluate_trailing_stop(
                trade, settings, current_price, pnl_percent
            )

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

        profit_target = settings["profit_target_percentage"]
        trailing_drop = settings["trailing_stop_drop_percentage"]

        # Arm the trailing stop if profit target is hit
        if not state["trailing_armed"] and pnl_percent >= profit_target:
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
            # Update peak price if we are making new highs
            if current_price > state["peak_price"]:
                state["peak_price"] = current_price

            # Check if price has dropped from the peak
            drop_from_peak_percent = (
                (state["peak_price"] - current_price) / state["peak_price"]
            ) * 100
            if drop_from_peak_percent >= trailing_drop:
                final_pnl = (
                    (current_price - trade["buy_price"]) / trade["buy_price"]
                ) * 100
                return f"🐉 Dragon strike! Profit of {final_pnl:.2f}% locked in."
        return None

    async def _check_and_open_new_trades(self, user_id: int, settings: dict):
        watchlist = db.get_watchlist(user_id)
        open_trade_symbols = {t["symbol"] for t in db.get_open_trades_by_user(user_id)}

        for symbol in watchlist:
            if symbol in open_trade_symbols:
                continue  # Already in a trade for this symbol

            decision = await self._get_gemini_buy_decision(symbol)
            if decision == "BUY":
                await self._buy_trade(user_id, symbol, settings)

    async def _get_gemini_buy_decision(self, symbol: str) -> str:
        """Consults Gemini for a buy/hold decision."""
        try:
            market_analysis = {}
            for tf in ["15m", "1h", "4h"]:
                klines = binance_client.get_historical_klines(
                    symbol=symbol, interval=tf, limit=100
                )
                if klines:
                    market_analysis[tf] = technical_analyzer.analyze_symbol(
                        symbol, klines
                    )

            if not market_analysis:
                return "HOLD"

            prompt = (
                f"You are an expert crypto trading analyst.\n"
                f"Analyze the following market data for {symbol} and decide if it is a good time to BUY now or if I should HOLD.\n"
                f"The data includes multiple timeframes (15m, 1h, 4h) with indicators like RSI, MACD, Bollinger Bands, etc.\n"
                f"Focus on identifying a strong buy signal. Look for bullish trends, momentum, and potential breakouts. A low RSI is good, but should be confirmed by other indicators.\n"
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
            logger.error(f"Error consulting Gemini for buy advice on {symbol}: {e}")
            return "HOLD"  # Default to holding on error

    async def _buy_trade(self, user_id: int, symbol: str, settings: dict):
        price = binance_client.get_current_price(symbol)
        if price is None:
            logger.error(f"Could not get price for {symbol} to execute buy.")
            return

        trade_size_usdt = settings["trade_size_usdt"]
        quantity = trade_size_usdt / price

        logger.info(f"Executing BUY for {symbol} for user {user_id} at {price}.")
        try:
            # order_result = binance_client.place_order(user_id, symbol, 'BUY', quantity)
            # Mocking order result for now
            order_result = {
                "symbol": symbol,
                "orderId": int(time.time() * 1000),
                "transactTime": int(time.time() * 1000),
                "price": str(price),
                "origQty": str(quantity),
                "executedQty": str(quantity),
                "cummulativeQuoteQty": str(trade_size_usdt),
                "status": "FILLED",
            }

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
            logger.error(f"Failed to execute buy for {symbol}: {e}")
            await self._notify_user(user_id, f"❌ Failed to buy {symbol}. Reason: {e}")

    async def _sell_trade(self, trade: dict, price: float, reason: str):
        user_id = trade["user_id"]
        symbol = trade["symbol"]
        logger.info(
            f"Executing SELL for {symbol} for user {user_id} at {price}. Reason: {reason}"
        )
        try:
            # binance_client.place_order(user_id, symbol, 'SELL', trade['quantity'])
            db.close_trade(trade["id"], price, reason)
            # Reset state for the symbol
            if user_id in self.user_states and symbol in self.user_states[user_id]:
                del self.user_states[user_id][symbol]
            await self._notify_user(user_id, f"🔴 Sold {symbol} at ${price:,.4f}.\n{reason}")
        except Exception as e:
            logger.error(f"Failed to execute sell for {symbol}: {e}")
            await self._notify_user(user_id, f"❌ Failed to sell {symbol}. Reason: {e}")

    async def _get_total_portfolio_value(self, user_id: int, settings: dict) -> float:
        try:
            if settings.get("trading_mode", "PAPER") == "PAPER":
                return db.get_user_paper_balance(user_id)
            else:
                return binance_client.get_total_account_balance_usdt(user_id)
        except Exception as e:
            logger.error(f"Failed to get portfolio value for user {user_id}: {e}")
            return 0.0

    async def _notify_user(self, user_id: int, message: str):
        try:
            await self.bot.send_message(chat_id=user_id, text=message)
        except Exception as e:
            logger.error(f"Failed to send notification to user {user_id}: {e}")
