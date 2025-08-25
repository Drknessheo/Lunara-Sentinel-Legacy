import requests
import logging

def fetch_top_binance_coins(limit=10, min_volume=1000000):
    """
    Fetch top coins by 24h USDT volume from Binance API.
    Returns a list of symbols (e.g., ['BTCUSDT', 'ETHUSDT', ...])
    """
    url = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        usdt_pairs = [d for d in data if d['symbol'].endswith('USDT') and float(d['quoteVolume']) > min_volume]
        usdt_pairs.sort(key=lambda x: float(x['quoteVolume']), reverse=True)
        top_coins = [d['symbol'] for d in usdt_pairs[:limit]]
        return top_coins
    except Exception as e:
        logging.error(f"Failed to fetch top coins: {e}")
        return []

# Example usage:
if __name__ == "__main__":
    print(fetch_top_binance_coins())
