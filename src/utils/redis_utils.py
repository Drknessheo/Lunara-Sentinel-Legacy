import redis

# Restore the correct relative import. 
# From this file (in utils), '..' refers to the parent package 'src'.
# This is the structurally correct way to access the config module.
from .. import config

# Initialize the client using the full URL from the config.
redis_client = redis.from_url(config.REDIS_URL, ssl_cert_reqs=None)

def delete_redis_slip(trade_id):
    """
    Deletes a trade slip from Redis using the standardized key format.
    """
    key = f"trade:{trade_id}"
    redis_client.delete(key)

def diagnose_slips_command(update, context):
    # This function was moved here from main to keep redis logic together.
    # (Implementation would be needed here)
    pass
