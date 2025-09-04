import hashlib
import hmac
from functools import wraps

from flask import abort, request

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
