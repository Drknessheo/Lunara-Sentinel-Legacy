import logging
from typing import Any, Dict

import pandas as pd

import indicators

logger = logging.getLogger(__name__)


def evaluate(
    slip: Dict[str, Any], settings: Dict[str, Any], market_df: pd.DataFrame = None
) -> str:
    """Evaluate a slip against user settings.

    Returns: 'buy', 'hold', or 'sell'
    """
    # If we have market_df, compute RSI; otherwise use slip-provided indicators
    rsi = None
    if market_df is not None and "close" in market_df:
        try:
            close = market_df["close"]
            rsi_series = indicators.calculate_rsi(close)
            rsi = float(rsi_series.iloc[-1])
        except Exception as e:
            logger.debug(f"Could not compute RSI from market_df: {e}")
    else:
        rsi = slip.get("indicators", {}).get("rsi")

    # Simple RSI-based rule
    buy_thresh = float(settings.get("RSI_BUY_THRESHOLD", 30))
    sell_thresh = float(settings.get("RSI_SELL_THRESHOLD", 70))

    if rsi is not None:
        if rsi < buy_thresh:
            return "buy"
        if rsi > sell_thresh:
            return "sell"

    return "hold"
