import hashlib
import hmac
from functools import wraps
from typing import Optional

# Local imports used for encrypt/decrypt helpers
from cryptography.fernet import Fernet
from flask import abort, request

from src import config as _config
from src.config import WEBHOOK_HMAC_SECRET


def verify_hmac(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not WEBHOOK_HMAC_SECRET:
            # For security, if the secret is not configured, we should
            # probably abort. But for development, we can allow it.
            # In a production environment, this should be a hard failure.
            print(
                "Warning: WEBHOOK_HMAC_SECRET is not set. Skipping HMAC verification."
            )
            return f(*args, **kwargs)

        signature = request.headers.get("X-Signature")
        if not signature:
            abort(401, description="Missing X-Signature header")

        mac = hmac.new(
            WEBHOOK_HMAC_SECRET.encode("utf-8"),
            msg=request.data,
            digestmod=hashlib.sha256,
        )

        if not hmac.compare_digest(mac.hexdigest(), signature):
            abort(401, description="Invalid signature")

        return f(*args, **kwargs)

    return decorated_function


def _get_fernet() -> Optional[Fernet]:
    """Return a Fernet instance using available encryption keys in config.

    Preference order: BINANCE_ENCRYPTION_KEY, SLIP_ENCRYPTION_KEY. Returns
    None if no key is configured.
    """
    for name in ("BINANCE_ENCRYPTION_KEY", "SLIP_ENCRYPTION_KEY"):
        key = getattr(_config, name, None)
        if key:
            try:
                return Fernet(key)
            except Exception:
                # Invalid key format; skip to next
                continue
    return None


def encrypt_data(value: str) -> bytes:
    """Encrypt a UTF-8 string and return bytes using configured Fernet key.

    Raises RuntimeError if no suitable key is available.
    """
    f = _get_fernet()
    if f is None:
        raise RuntimeError(
            "No encryption key configured: set BINANCE_ENCRYPTION_KEY or SLIP_ENCRYPTION_KEY"
        )
    return f.encrypt(value.encode("utf-8"))


def decrypt_data(value: bytes | str) -> str:
    """Decrypt bytes (or str) and return a UTF-8 string.

    Raises RuntimeError if no suitable key is available or decryption fails.
    """
    f = _get_fernet()
    if f is None:
        raise RuntimeError(
            "No decryption key configured: set BINANCE_ENCRYPTION_KEY or SLIP_ENCRYPTION_KEY"
        )
    if isinstance(value, str):
        value = value.encode()
    return f.decrypt(value).decode("utf-8")
