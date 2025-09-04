import logging
import os

import requests

logger = logging.getLogger(__name__)


def send_admin_alert(text: str) -> bool:
    """Send a simple Telegram message to the configured ADMIN_USER_ID using BOT_TOKEN.

    Returns True on success, False otherwise. Non-fatal by design.
    """
    bot_token = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    admin_id = os.environ.get("ADMIN_USER_ID")
    if not bot_token or not admin_id:
        logger.debug("Admin alert skipped: BOT_TOKEN or ADMIN_USER_ID not set")
        return False

    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": admin_id, "text": text, "parse_mode": "HTML"}
        resp = requests.post(url, data=payload, timeout=5)
        if resp.ok:
            return True
        logger.warning("Admin alert failed: %s %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning("Admin alert exception: %s", e)
    return False
