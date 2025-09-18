import datetime


def get_trade_size(account_balance, min_trade=5.0, risk_pct=0.05):
    size = max(account_balance * risk_pct, min_trade)
    return min(size, account_balance)


# --- Market Crash/Big Buyer Shield ---
def is_market_crash_or_big_buyer(prices: dict) -> bool:
    """Detects sudden market crash or big buyer activity."""
    btc_now = prices.get("BTCUSDT")
    btc_prev = prices.get("BTCUSDT_15min_ago")

    if btc_now is None or btc_prev is None:
        return False

    if btc_now <= btc_prev * 0.95:  # 5% drop
        return True
    if btc_now >= btc_prev * 1.05:  # 5% jump
        return True

    return False


def get_atr_stop(entry_price, atr, multiplier=1.5):
    return entry_price - multiplier * atr

def adjust_stop_loss(current_price, entry_price, initial_stop_loss, user_take_profit_pct=0.08):
    """
    Dynamically adjusts the stop-loss based on the current profit.
    This is the Ministry's wisdom in action.
    """
    if not all([current_price, entry_price, initial_stop_loss]):
        return initial_stop_loss

    profit_pct = (current_price - entry_price) / entry_price

    # The Ministry's wisdom:
    # If profit is getting close to the user's take profit level,
    # tighten the stop-loss to protect the gains.
    if profit_pct >= user_take_profit_pct - 0.005: # within 0.5% of user's TP
        # Secure profits, move stop loss to just below the take profit level
        # This will result in a small profit if the price reverses.
        new_stop_loss = entry_price * (1 + user_take_profit_pct - 0.01) # e.g. at 7% if TP is 8%
        return max(new_stop_loss, initial_stop_loss) # Ensure we don't loosen the stop-loss

    # The Emperor's suggestion:
    # If the coin is up 10%, but the user's take profit was 8%,
    # the Ministry steps in and changes the stop-loss to lock in more profit.
    if profit_pct >= 0.10 and user_take_profit_pct == 0.08:
        # Secure a large portion of the gains
        new_stop_loss = entry_price * (1 + 0.095) # lock in 9.5% profit
        return max(new_stop_loss, initial_stop_loss)

    # If the price is moving favorably, trail the stop-loss up.
    # For now, a simple trailing stop-loss can be implemented.
    # Let's say we trail by 2%
    if profit_pct > 0.02:
        new_stop_loss = current_price * 0.98 # Trail by 2%
        return max(new_stop_loss, initial_stop_loss)

    return initial_stop_loss

def manage_open_trade(trade, current_price):
    """
    Manages an open trade by adjusting the stop-loss.
    """
    # We need to get the trade details from the trade object.
    # Assuming the trade object has 'entry_price', 'stop_loss', 'take_profit_pct' attributes.
    # This is a placeholder for the actual trade object structure.
    entry_price = trade.get('entry_price')
    initial_stop_loss = trade.get('stop_loss')
    user_take_profit_pct = trade.get('take_profit_pct', 0.08) # Default to 8% if not set

    new_stop_loss = adjust_stop_loss(current_price, entry_price, initial_stop_loss, user_take_profit_pct)

    if new_stop_loss != initial_stop_loss:
        # Here we would update the trade object with the new stop-loss
        # and potentially place a new order on the exchange.
        # For now, we just log the event.
        print(f"Ministry has adjusted stop-loss for trade {trade.get('id')} from {initial_stop_loss} to {new_stop_loss}")
        trade['stop_loss'] = new_stop_loss

    return trade

def update_daily_pl(trade_result, db):
    today = datetime.date.today().isoformat()
    db.update_daily_pl(today, trade_result)


def should_pause_trading(db, account_balance, max_drawdown_pct=0.10):
    today = datetime.date.today().isoformat()
    daily_pl = db.get_daily_pl(today)
    return daily_pl < -account_balance * max_drawdown_pct
