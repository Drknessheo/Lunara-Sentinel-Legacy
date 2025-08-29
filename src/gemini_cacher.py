import os
import time
import json
import logging
import threading
import httpx
from . import config

try:
    import redis
except ImportError:
    redis = None

logger = logging.getLogger(__name__)

# --- Gemini Cache (Redis optional) ---

redis_client = None
if config.GEMINI_API_KEY and redis:
    try:
        # Assuming REDIS_URL is defined in your config.py or .env
        redis_url = getattr(config, 'REDIS_URL', None)
        if redis_url:
            redis_client = redis.from_url(redis_url)
            logger.info("Connected to Redis for Gemini cache")
    except Exception as e:
        logger.warning(f"Redis connection failed, using file cache: {e}")
        redis_client = None

GEMINI_CACHE_FILE = "gemini_cache.json"
GEMINI_CACHE_TTL = 300  # 5 minutes, can be moved to config

def get_cache(key: str):
    if redis_client:
        try:
            value = redis_client.get(key)
            if value:
                return json.loads(value)
        except redis.exceptions.RedisError as e:
            logger.error(f"Redis GET error: {e}")
            # Fallback to file cache if redis fails
            return get_file_cache(key)
    return get_file_cache(key)

def set_cache(key: str, value: dict):
    if redis_client:
        try:
            redis_client.set(key, json.dumps(value), ex=GEMINI_CACHE_TTL)
        except redis.exceptions.RedisError as e:
            logger.error(f"Redis SET error: {e}")
            # Fallback to file cache if redis fails
            set_file_cache(key, value)
    else:
        set_file_cache(key, value)

def get_file_cache(key: str):
    if not os.path.exists(GEMINI_CACHE_FILE):
        return None
    try:
        with open(GEMINI_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        entry = cache.get(key)
        if entry and (time.time() - entry["ts"] < GEMINI_CACHE_TTL):
            return entry["data"]
    except (IOError, json.JSONDecodeError) as e:
        logger.error(f"File cache read error: {e}")
    return None

def set_file_cache(key: str, value: dict):
    cache = {}
    if os.path.exists(GEMINI_CACHE_FILE):
        try:
            with open(GEMINI_CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except (IOError, json.JSONDecodeError):
            pass  # Start with a fresh cache if file is corrupt
    
    cache[key] = {"ts": time.time(), "data": value}
    
    try:
        with open(GEMINI_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except IOError as e:
        logger.error(f"File cache write error: {e}")


# --- Gemini API Call Logic ---

gemini_key_idx = 0
gemini_key_lock = threading.Lock()

def get_next_gemini_key():
    global gemini_key_idx
    with gemini_key_lock:
        if not config.GEMINI_API_KEY:
            return None
        # This part assumes GEMINI_API_KEY is a list of keys in config.py
        # If it's a single key, we just return that.
        # For now, let's assume it's a single key as per the current project structure.
        return config.GEMINI_API_KEY

async def ask_gemini_for_symbol(symbol: str, prompt_extra: str = "") -> dict:
    cache_key = f"gemini:{symbol}"
    cached = get_cache(cache_key)
    if cached:
        logger.info(f"Gemini cache hit for {symbol}")
        return cached

    logger.info(f"Gemini cache miss for {symbol}, querying API...")
    api_key = get_next_gemini_key()
    if not api_key:
        return {"note": "no_gemini_key"}

    # Assuming a generic API endpoint, this should be in config.py
    gemini_url = getattr(config, 'GEMINI_API_URL', 'https://api.gemini.example/analysis')

    headers = {"Authorization": f"Bearer {api_key}"}
    data = {"symbol": symbol, "context": prompt_extra}
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(gemini_url, json=data, headers=headers, timeout=20)
            resp.raise_for_status()
            result = resp.json()
            set_cache(cache_key, result)
            return result
        except httpx.HTTPStatusError as e:
            logger.warning(f"Gemini API call failed for {symbol} with status {e.response.status_code}: {e.response.text}")
            return {"note": "error", "error": str(e)}
        except httpx.RequestError as e:
            logger.warning(f"Gemini request failed for {symbol}: {e}")
            return {"note": "error", "error": str(e)}
