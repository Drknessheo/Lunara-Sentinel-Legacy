import numpy as np
import pandas as pd

def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period=14):
    """Calculates the Average True Range (ATR) using pandas."""
    high_low = high - low
    high_close = np.abs(high - close.shift())
    low_close = np.abs(low - close.shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(window=period, min_periods=period).mean()
    return atr

def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / (avg_loss.replace(0, 1e-8))
    rsi = 100 - (100 / (1 + rs))
    return rsi

def get_volatility_based_ladder(df: pd.DataFrame, base_multiplier=1.5, steps=3):
    """
    Generates a dynamic stop-loss ladder based on volatility (ATR).
    
    df: DataFrame with columns ['high', 'low', 'close']
    base_multiplier: ATR multiplier for first ladder step
    steps: number of DSLA steps
    """
    if len(df) < 15: # Need at least 14 periods for ATR + 1 current
        return []

    atr = calculate_atr(df['high'], df['low'], df['close'])
    
    # Ensure we have a valid ATR value to proceed
    if atr.empty or pd.isna(atr.iloc[-1]):
        return []

    latest_atr = atr.iloc[-1]
    latest_close = df['close'].iloc[-1]

    if latest_close == 0:
        return [] # Avoid division by zero

    latest_atr_pct = (latest_atr / latest_close) * 100

    ladder = []
    for i in range(1, steps + 1):
        profit_target = round(latest_atr_pct * base_multiplier * i, 2)
        # Set stop-loss to half of the profit target for that step
        stop_loss = round(profit_target * 0.5, 2)
        ladder.append({'profit': profit_target, 'sl': stop_loss})
    
    return ladder

# Keep the old calc_atr function for now to avoid breaking other parts of the code
# that might still use it. I'll rename it to calc_atr_numpy.
def calc_atr_numpy(klines, period=14):
    highs = np.array([float(k[2]) for k in klines])
    lows = np.array([float(k[3]) for k in klines])
    closes = np.array([float(k[4]) for k in klines])
    
    # Correctly calculate True Range
    trs = []
    for i in range(1, len(klines)):
        high = highs[i]
        low = lows[i]
        prev_close = closes[i-1]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        
    if len(trs) < period:
        return 0 # Not enough data for ATR

    atr = np.mean(trs[-period:])
    return atr