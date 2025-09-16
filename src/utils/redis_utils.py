import redis
from config import REDIS_URL, REDIS_TOKEN

redis_client = redis.from_url(REDIS_URL, password=REDIS_TOKEN, ssl_cert_reqs=None)

def delete_redis_slip(trade_id):
    """
    Deletes a trade slip from Redis.
    """
    redis_client.delete(f"trade_slip:{trade_id}")
