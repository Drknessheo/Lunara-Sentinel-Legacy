"""Small utilities for sanitizing and masking Redis URLs.

These helpers ensure a Redis URL includes a scheme (redis:// or rediss://)
so `redis.from_url` won't raise on Upstash-style URLs that omit the scheme.
"""
from __future__ import annotations
import re
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def sanitize_redis_url(url: Optional[str]) -> str:
    """Ensure the Redis URL contains a supported scheme.

    Rules:
    - If url is falsy, return the default local Redis url.
    - If url already starts with redis://, rediss://, or unix://, return as-is.
    - If url starts with '//' (Upstash-like), prefix with 'redis:' so it becomes 'redis://...'
    - Otherwise, prefix with 'redis://' (stripping any leading slashes).

    This is intentionally conservative: it does not attempt to validate credentials
    or perform DNS lookups. It only adjusts the textual scheme so `redis.from_url`
    accepts it.
    """
    # Determine TLS preference from environment variable if set
    tls_env = os.getenv("REDIS_USE_TLS")
    if tls_env is not None:
        tls_env_val = str(tls_env).strip().lower()
        prefer_tls = tls_env_val in ("1", "true", "yes", "on")
    else:
        prefer_tls = None

    if not url:
        # Choose default scheme based on TLS preference if set
        if prefer_tls:
            return "rediss://localhost:6379/0"
        return "redis://localhost:6379/0"

    url = str(url).strip()
    # If a valid scheme already present, return unchanged
    if re.match(r'^(redis://|rediss://|unix://)', url, re.IGNORECASE):
        return url

    # Heuristic: prefer rediss for Upstash hosts unless REDIS_USE_TLS explicitly set to false
    if prefer_tls is None and 'upstash' in url.lower():
        prefer_tls = True

    # Upstash sometimes supplies URLs that begin with //user:pass@host:port
    if url.startswith("//"):
        if prefer_tls:
            return "rediss:" + url
        return "redis:" + url

    # Otherwise, remove extra leading slashes and prefix with chosen scheme
    if prefer_tls:
        return "rediss://" + url.lstrip("/")
    return "redis://" + url.lstrip("/")


def mask_redis_url(url: Optional[str]) -> str:
    """Return a masked Redis URL suitable for logs (hide credentials).

    Examples:
      redis://user:pass@host:6379 -> redis://***:***@host:6379
      redis://host:6379 -> redis://host:6379
    """
    if not url:
        return "redis://<none>"
    u = str(url)
    # Split protocol
    if '://' in u:
        proto, rest = u.split('://', 1)
    else:
        proto, rest = 'redis', u

    if '@' in rest:
        creds, host = rest.split('@', 1)
        return f"{proto}://***:***@{host}"
    return f"{proto}://{rest}"


def get_redis_client(url: Optional[str] = None, **kwargs):
    """Return a redis client created from a sanitized URL.

    Accepts the same keyword args as `redis.from_url`, e.g. `decode_responses=True`.
    """
    import redis as _redis
    sanitized = sanitize_redis_url(url)
    masked = mask_redis_url(sanitized)
    # Log masked URL so callers can see where we're connecting without leaking creds
    try:
        logger.info(f"Creating Redis client for {masked}")
    except Exception:
        pass
    return _redis.from_url(sanitized, **kwargs)
