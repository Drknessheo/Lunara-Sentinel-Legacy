
import logging
import pandas as pd
import pandas_ta as ta

logger = logging.getLogger(__name__)

def analyze_symbol(symbol: str, kline_data: list, settings: dict) -> dict:
    """
    Calculates multiple technical indicators and generates qualitative "symptoms"
    based on the user's specific settings.

    Args:
        symbol: The symbol being analyzed.
        kline_data: A list of k-lines from the Binance API.
        settings: The user's effective autotrade settings.

    Returns:
        A dictionary containing indicators and a nested 'symptoms' dictionary.
    """
    if not kline_data or len(kline_data) < 30:
        logger.warning(f"Not enough data to analyze {symbol}. Have {len(kline_data) if kline_data else 0} candles.")
        return {}

    try:
        # --- Memory Optimization: Define columns and dtypes ---
        kline_columns = [
            'timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time',
            'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume',
            'taker_buy_quote_asset_volume', 'ignore'
        ]
        used_cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        
        df = pd.DataFrame(kline_data, columns=kline_columns)
        df = df[used_cols]

        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce').astype('float32')

        df.dropna(inplace=True)
        if len(df) < 30:
            logger.warning(f"Insufficient valid data points for {symbol} after cleaning.")
            return {}

        # --- Calculate all desired indicators ---
        df.ta.rsi(length=14, append=True)
        df.ta.macd(fast=12, slow=26, signal=9, append=True)
        df.ta.bbands(length=20, std=2, append=True)

        # --- Build Result Incrementally and with Validation ---
        latest_indicators = {}
        symptoms = {}
        latest = df.iloc[-1]
        
        # --- RSI Analysis ---
        if 'RSI_14' in latest and pd.notna(latest['RSI_14']):
            rsi_value = round(latest['RSI_14'], 2)
            latest_indicators['rsi'] = rsi_value
            # Generate Symptom based on user setting
            rsi_buy_threshold = float(settings.get('rsi_buy', 30.0))
            if rsi_value < rsi_buy_threshold:
                symptoms['rsi_symptom'] = f"oversold_entry_point (RSI {rsi_value} < {rsi_buy_threshold})"
            elif rsi_value > 70:
                symptoms['rsi_symptom'] = f"overbought (RSI {rsi_value} > 70)"

        # --- MACD Analysis ---
        if all(c in latest for c in ['MACD_12_26_9', 'MACDh_12_26_9', 'MACDs_12_26_9']) and pd.notna(latest['MACD_12_26_9']):
            latest_indicators['macd'] = round(latest['MACD_12_26_9'], 2)
            latest_indicators['macd_signal'] = round(latest['MACDs_12_26_9'], 2)
            # Generate Trend Symptom
            if latest['MACD_12_26_9'] > latest['MACDs_12_26_9'] and df['MACDh_12_26_9'].iloc[-2] < 0:
                 symptoms['trend_symptom'] = "potential_uptrend_reversal (MACD crossed above signal)"
            elif latest['MACD_12_26_9'] < latest['MACDs_12_26_9']:
                 symptoms['trend_symptom'] = "downtrend_active (MACD below signal)"
            else:
                 symptoms['trend_symptom'] = "uptrend_active (MACD above signal)"


        # --- Bollinger Bands Analysis ---
        if all(c in latest for c in ['BBU_20_2', 'BBL_20_2']) and pd.notna(latest['BBU_20_2']):
            latest_indicators['bollinger_upper'] = round(latest['BBU_20_2'], 2)
            latest_indicators['bollinger_lower'] = round(latest['BBL_20_2'], 2)
            # Generate Symptom
            if latest['close'] < latest['BBL_20_2']:
                symptoms['volatility_symptom'] = "breaking_support_potential_reversal (Price below lower Bollinger Band)"
            elif latest['close'] > latest['BBU_20_2']:
                symptoms['volatility_symptom'] = "breaking_resistance (Price above upper Bollinger Band)"

        if not latest_indicators:
            logger.warning(f"Technical analysis for {symbol} yielded no valid indicators.")
            del df # Memory Optimization
            return {}

        # Attach the symptoms to the final report
        latest_indicators['symptoms'] = symptoms
        del df # Memory Optimization
        return latest_indicators

    except Exception as e:
        logger.error(f"An unexpected error occurred during symptom analysis for {symbol}: {e}", exc_info=True)
        return {}
