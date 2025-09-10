import os
import re
from typing import Any

"""Small utility to mask secret environment values in log messages.

The function is intentionally conservative: it only masks literal occurrences
of environment variable values whose names either match an explicit allowlist
or contain KEY/TOKEN/SECRET/SALT.
"""

EXPLICIT_SECRETS = {
    "BINANCE_API_KEY",
    "BINANCE_SECRET_KEY",
    "BINANCE_ENCRYPTION_KEY",
    "SLIP_ENCRYPTION_KEY",
    "SLIP_ENCRYPTION_SALT",
    "FB_ACCESS_TOKEN",
    "REDIS_TOKEN",
    "WEBHOOK_HMAC_SECRET",
    "ADMIN_PANEL_TOKEN",
    "PROMOTION_WEBHOOK_SECRET",
    "SMOKE_WEBHOOK_SECRET",
    "TELEGRAM_BOT_TOKEN",
    "SANDPAPER_ENCRYPTION_KEY",
}


def mask_secrets(value: Any) -> str:
    """Return `value` with secret environment variable values replaced by a mask.

    If `value` is not a string it is returned unchanged.
    """
    if not isinstance(value, str):
        return value

    candidates = {
        k
        for k in os.environ.keys()
        if any(x in k.upper() for x in ("KEY", "TOKEN", "SECRET", "SALT"))
    }
    candidates.update(EXPLICIT_SECRETS)

    masked = value
    for name in candidates:
        secret = os.environ.get(name)
        if not secret:
            continue
        masked = re.sub(re.escape(secret), "**** MASKED ****", masked)

    return masked


__all__ = ["mask_secrets"]
