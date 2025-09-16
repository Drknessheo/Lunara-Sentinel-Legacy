import redis
from .. import config

# The redis.from_url() function can parse the full connection details,
# including a password, directly from the REDIS_URL string.
# This removes the need for a separate REDIS_TOKEN and standardizes
# the connection method with other parts of the application.
redis_client = redis.from_url(config.REDIS_URL, ssl_cert_reqs=None)

def delete_redis_slip(trade_id):
    """
    Deletes a trade slip from Redis.
    Note: The key format must match the one used when creating the slip.
    """
    # Assuming the key format is 'trade:<trade_id>' as seen in other parts of the code
    redis_client.delete(f"trade:{trade_id}")
