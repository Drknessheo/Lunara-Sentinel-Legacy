"""Compatibility shim for trade guard used by src.trade during tests.

Provides a minimal TradeValidator with an is_trade_valid check so unit tests
that import `src.trade` don't fail when the full implementation isn't present.
"""


class TradeValidator:
    @staticmethod
    def is_trade_valid(symbol: str, quantity: float, price: float, **kwargs) -> bool:
        # Minimal permissive validator used in tests.
        return True
