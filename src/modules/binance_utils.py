import requests

def get_binance_min_notional(symbol):
    """Fetches the minimum notional value for a symbol from Binance exchange info."""
    url = "https://api.binance.com/api/v3/exchangeInfo"
    response = requests.get(url)
    data = response.json()
    for s in data['symbols']:
        if s['symbol'] == symbol:
            for f in s['filters']:
                if f['filterType'] == 'MIN_NOTIONAL':
                    return float(f['minNotional'])
    return None
