
"""
Facade for the Binance module.

This module provides a simplified interface to the Binance dragons.
"""

from .binance.client import (
    ensure_binance_client,
    BINANCE_AVAILABLE,
    BINANCE_INIT_ERROR,
    client,
)
from .binance.market_data import (
    get_symbol_info,
    get_historical_klines,
    get_current_price,
    TradeError,
)
from .binance.account import (
    get_all_spot_balances,
    get_total_account_balance_usdt,
)

__all__ = [
    "ensure_binance_client",
    "BINANCE_AVAILABLE",
    "BINANCE_INIT_ERROR",
    "client",
    "get_symbol_info",
    "get_historical_klines",
    "get_current_price",
    "get_all_spot_balances",
    "get_total_account_balance_usdt",
    "TradeError",
]
