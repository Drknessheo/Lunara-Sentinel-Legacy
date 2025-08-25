import datetime

def get_trade_size(account_balance, min_trade=5.0, risk_pct=0.05):
    size = max(account_balance * risk_pct, min_trade)
    return min(size, account_balance)

# --- Market Crash/Big Buyer Shield ---
def is_market_crash_or_big_buyer(prices: dict) -> bool:
    """Detects sudden market crash or big buyer activity."""
    btc_now = prices.get('BTCUSDT')
    btc_prev = prices.get('BTCUSDT_15min_ago')

    if btc_now is None or btc_prev is None:
        return False

    if btc_now <= btc_prev * 0.95:  # 5% drop
        return True
    if btc_now >= btc_prev * 1.05:  # 5% jump
        return True

    return False

def get_atr_stop(entry_price, atr, multiplier=1.5):
    return entry_price - multiplier * atr

def update_daily_pl(trade_result, db):
    today = datetime.date.today().isoformat()
    db.update_daily_pl(today, trade_result)

def should_pause_trading(db, account_balance, max_drawdown_pct=0.10):
    today = datetime.date.today().isoformat()
    daily_pl = db.get_daily_pl(today)
    return daily_pl < -account_balance * max_drawdown_pct
