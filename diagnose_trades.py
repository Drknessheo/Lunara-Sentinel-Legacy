import redis
import os

# Load Redis connection from environment or config
REDIS_URL = os.getenv("REDIS_URL", "rediss://***:***@measured-whale-51756.upstash.io:6379")
r = redis.from_url(REDIS_URL)

# Trade IDs to inspect
trade_ids = [49, 51, 52, 54, 55, 58, 59]

print("Trade Diagnostic Report")
print("=" * 40)

for trade_id in trade_ids:
    quantity_key = f"trade:{trade_id}:quantity"
    quota_key = f"trade:{trade_id}:sell_quota_met"
    status_key = f"trade:{trade_id}:status"

    quantity = r.get(quantity_key)
    quota_met = r.get(quota_key)
    status = r.get(status_key)

    print(f"Trade ID: {trade_id}")
    print(f"  Quantity: {quantity if quantity else 'Missing'}")
    print(f"  Sell Quota Met: {quota_met if quota_met else 'Not Set'}")
    print(f"  Status: {status if status else 'Unknown'}")
    print("-" * 40)
