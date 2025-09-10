import logging
import os

import requests

from .logging_utils import mask_secrets

logger = logging.getLogger(__name__)


def format_facebook_post(user_id, reviews):
    if not reviews:
        return f"User {user_id} has no performance reviews yet."
    lines = [f"Performance summary for {user_id}:\n"]
    for r in reviews[:5]:
        ts = r.get("timestamp")
        rating = r.get("rating")
        text = r.get("review_text")
        lines.append(f"- {rating}/10 at {ts}: {text}")
    return "\n".join(lines)


def post_to_facebook(page_access_token: str, page_id: str, message: str):
    """Simulate posting to Facebook. If PAGE_ACCESS_TOKEN and PAGE_ID are set and requests works,
    attempt to POST to the Graph API. Otherwise log the message.
    """
    safe_message = mask_secrets(message)
    if not page_access_token or not page_id:
        logger.warning("Facebook credentials missing; skipping post.")
        print(safe_message)
        return False

    url = f"https://graph.facebook.com/{page_id}/feed"
    payload = {"message": message, "access_token": page_access_token}
    try:
        r = requests.post(url, data=payload, timeout=10)
        r.raise_for_status()
        logger.info("Posted to Facebook page %s", page_id)
        return True
    except Exception as e:
        logger.exception("Failed to post to Facebook: %s", e)
        print(safe_message)
        return False
