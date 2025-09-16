import json
import redis
import config

class GeminiCacher:
    def __init__(self, redis_url=config.REDIS_URL):
        self.redis = redis.from_url(redis_url)

    def get(self, key):
        data = self.redis.get(key)
        if data:
            return json.loads(data)
        return None

    def set(self, key, value, ex=3600):
        self.redis.set(key, json.dumps(value), ex=ex)
