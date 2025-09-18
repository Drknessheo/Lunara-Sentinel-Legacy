# blueprint/lunessasignels/lunara-bot/src/technical_analyzer.py
"""
This module provides functions for calculating various technical indicators.
"""

import logging
import pandas as pd
import pandas_ta as ta

logger = logging.getLogger(__name__)

def analyze_symbol(kline_data: list) -> dict:
    """
    Calculates multiple technical indicators for a given set of k-line data.

    Args:
        kline_data: A list of k-lines from the Binance API.

    Returns:
        A dictionary containing the calculated indicators for the latest candle,
        or an empty dictionary if analysis fails.
    """
    if not kline_data or len(kline_data) < 30: # Increased data requirement
        return {}

    try:
        # Convert to pandas DataFrame
        df = pd.DataFrame(kline_data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume', 
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])

        # Ensure correct data types
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col])

        # Calculate indicators using pandas_ta
        df.ta.rsi(length=14, append=True)
        df.ta.macd(fast=12, slow=26, signal=9, append=True)
        df.ta.bbands(length=20, std=2, append=True)

        # --- Defensive check for indicator columns ---
        required_cols = ['RSI_14', 'MACD_12_26_9', 'MACDs_12_26_9', 'BBU_20_2.0', 'BBL_20_2.0']
        if not all(col in df.columns for col in required_cols):
            logger.warning(f"Could not calculate all required indicators. Columns missing in DataFrame.")
            return {}
        # --------------------------------------------

        # Get the latest indicator values
        latest_indicators = {
            'rsi': round(df['RSI_14'].iloc[-1], 2),
            'macd': round(df['MACD_12_26_9'].iloc[-1], 2),
            'macd_signal': round(df['MACDs_12_26_9'].iloc[-1], 2),
            'bollinger_upper': round(df['BBU_20_2.0'].iloc[-1], 2),
            'bollinger_lower': round(df['BBL_20_2.0'].iloc[-1], 2),
            'is_breaking_bollinger_upper': df['close'].iloc[-1] > df['BBU_20_2.0'].iloc[-1],
            'is_breaking_bollinger_lower': df['close'].iloc[-1] < df['BBL_20_2.0'].iloc[-1],
        }

        return latest_indicators

    except KeyError as e:
        logger.error(f"KeyError during indicator analysis: {e}. This likely means an indicator column was not generated.")
        return {}
    except Exception as e:
        logger.error(f"Failed to analyze symbol data: {e}", exc_info=True)
        return {}
