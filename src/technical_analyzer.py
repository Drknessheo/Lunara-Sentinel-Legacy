
import logging
import pandas as pd
import pandas_ta as ta

logger = logging.getLogger(__name__)

def analyze_symbol(symbol: str, kline_data: list) -> dict:
    """
    Calculates multiple technical indicators for a given set of k-line data.
    This function is designed to be resilient, returning any indicators that can be successfully calculated
    even if others fail.

    Args:
        symbol: The symbol being analyzed.
        kline_data: A list of k-lines from the Binance API.

    Returns:
        A dictionary containing the successfully calculated indicators for the latest candle.
    """
    if not kline_data or len(kline_data) < 30: # A reasonable minimum for common indicators
        logger.warning(f"Not enough data to analyze {symbol}. Have {len(kline_data) if kline_data else 0} candles, need at least 30.")
        return {}

    try:
        df = pd.DataFrame(kline_data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume', 
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])

        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        df.dropna(subset=['open', 'high', 'low', 'close', 'volume'], inplace=True)
        if len(df) < 30:
            logger.warning(f"Insufficient valid data points for {symbol} after cleaning. Needed 30, have {len(df)}.")
            return {}

        # --- Calculate all desired indicators ---
        df.ta.rsi(length=14, append=True)
        df.ta.macd(fast=12, slow=26, signal=9, append=True)
        df.ta.bbands(length=20, std=2, append=True)

        # --- Build Result Incrementally and with Validation ---
        latest_indicators = {}
        latest = df.iloc[-1]

        # RSI
        if 'RSI_14' in latest and pd.notna(latest['RSI_14']):
            latest_indicators['rsi'] = round(latest['RSI_14'], 2)

        # MACD
        if all(c in latest for c in ['MACD_12_26_9', 'MACDs_12_26_9']) and pd.notna(latest['MACD_12_26_9']):
            latest_indicators['macd'] = round(latest['MACD_12_26_9'], 2)
            latest_indicators['macd_signal'] = round(latest['MACDs_12_26_9'], 2)

        # Bollinger Bands
        if all(c in latest for c in ['BBU_20_2', 'BBL_20_2']) and pd.notna(latest['BBU_20_2']):
            latest_indicators['bollinger_upper'] = round(latest['BBU_20_2'], 2)
            latest_indicators['bollinger_lower'] = round(latest['BBL_20_2'], 2)
            latest_indicators['is_breaking_bollinger_upper'] = latest['close'] > latest['BBU_20_2']
            latest_indicators['is_breaking_bollinger_lower'] = latest['close'] < latest['BBL_20_2']
        
        if not latest_indicators:
             logger.warning(f"Technical analysis for {symbol} yielded no valid indicators despite having sufficient data.")

        return latest_indicators

    except Exception as e:
        logger.error(f"An unexpected error occurred during analysis for {symbol}: {e}", exc_info=True)
        return {}
