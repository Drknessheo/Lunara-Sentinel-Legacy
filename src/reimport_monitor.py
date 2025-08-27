
import os
import sys
import logging
import redis
from datetime import datetime

# Ensure the script can find the 'src' modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config
from src import trade

# --- Logger Setup ---
logger = logging.getLogger("reimport_monitor")
logger.setLevel(logging.INFO)
if not logger.hasHandlers():
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# --- Redis Connection ---
try:
    redis_client = redis.from_url(config.REDIS_URL, ssl_cert_reqs='none', decode_responses=True)
    logger.info("Successfully connected to Redis.")
except Exception as e:
    logger.error(f"Failed to connect to Redis: {e}")
    redis_client = None

def run_reimport_scan():
    """
    Scans the Binance spot wallet for valuable assets that are not currently monitored
    in Redis and re-imports them to prevent orphanage.
    """
    if not redis_client:
        logger.error("Cannot run scan: Redis client is not available.")
        return

    logger.info("Starting wallet scan for re-import...")
    user_id = config.ADMIN_USER_ID
    if not user_id:
        logger.error("Cannot run scan: ADMIN_USER_ID not set in config.")
        return

    try:
        balances = trade.get_all_spot_balances(user_id)
        if balances is None:
            logger.error("Could not retrieve wallet balances. Check API keys and permissions.")
            return
    except Exception as e:
        logger.error(f"An error occurred while fetching wallet balances: {e}")
        return

    reimport_threshold = 4.50  # As per instructions ($5.00 with 10% buffer)
    reimported_count = 0

    for asset in balances:
        asset_symbol = asset['asset']
        if asset_symbol in ['USDT', 'USDC', 'FDUSD']:  # Ignore stablecoins
            continue

        total_balance = float(asset['free']) + float(asset['locked'])
        if total_balance <= 0:
            continue

        # Construct the trading symbol (e.g., ETH -> ETHUSDT)
        symbol = f"{asset_symbol}USDT"

        try:
            current_price = trade.get_current_price(symbol)
            if not current_price:
                logger.warning(f"Could not get price for {symbol}, skipping.")
                continue

            value_usdt = total_balance * current_price

            # Step 1: Wallet Scan & Value Check
            if value_usdt >= reimport_threshold:
                logger.info(f"Found valuable asset: {symbol} | Value: ${value_usdt:.2f}")

                # Step 2: Trade Validation against Redis
                redis_key = f"trade_status:{symbol}"
                if not redis_client.exists(redis_key):
                    logger.info(f"{symbol} not found in Redis. Proceeding with re-import.")

                    # Step 3: Redis Sync
                    trade_data = {
                        "value": f"{value_usdt:.2f}",
                        "status": "pending_monitoring", # A clear status for re-imported trades
                        "source": "reimported_wallet_scan",
                        "timestamp": datetime.now().isoformat()
                    }
                    redis_client.hset(redis_key, mapping=trade_data)
                    reimported_count += 1

                    # Step 5: Confirmation Log
                    logger.info(f"✅ SUCCESS: {symbol} re-imported and is now pending monitoring. Details: {trade_data}")

                else:
                    logger.info(f"{symbol} is already monitored in Redis. Skipping.")

        except Exception as e:
            logger.error(f"An error occurred while processing {symbol}: {e}")

    logger.info(f"Re-import scan complete. Re-imported {reimported_count} new asset(s).")

if __name__ == "__main__":
    run_reimport_scan()
