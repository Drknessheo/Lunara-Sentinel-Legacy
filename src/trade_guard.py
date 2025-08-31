import logging


class TradeValidator:
    MIN_NOTIONAL = 5.0  # Binance minimum in USDT

    @staticmethod
    def is_trade_valid(
        symbol: str, quantity: float, price: float, user_id=None, slip_id=None
    ) -> bool:
        notional = quantity * price
        if notional < TradeValidator.MIN_NOTIONAL:
            tag = f"[symbol={symbol}]"
            if user_id is not None:
                tag += f"[user_id={user_id}]"
            if slip_id is not None:
                tag += f"[slip_id={slip_id}]"
            logging.warning(
                f"{tag} Trade skipped: Notional value {notional:.2f} < {TradeValidator.MIN_NOTIONAL} USDT"
            )
            return False
        return True

    @staticmethod
    def adjust_quantity_to_min_notional(price: float) -> float:
        """
        Returns the minimum quantity needed to meet the notional threshold, rounded to 6 decimals.
        """
        return round((TradeValidator.MIN_NOTIONAL + 0.01) / price, 6)
