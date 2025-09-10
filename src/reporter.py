import logging
from datetime import datetime

from .logging_utils import mask_secrets

logger = logging.getLogger(__name__)


def format_performance_report(user_id, reviews):
    """Return a formatted Markdown report string for a user's reviews."""
    parts = [f"**Performance Review Report for User:** `{user_id}`\n\n"]
    if not reviews:
        parts.append("No reviews found to report.")
    else:
        for review in reviews:
            ts = review.get("timestamp") or 0
            timestamp = datetime.fromtimestamp(ts)
            rating = review.get("rating", "N/A")
            text = review.get("review_text", "No text provided.")
            parts.append(f"• **Date:** {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
            parts.append(f"• **Rating:** {rating}/10")
            parts.append(f"• **Notes:** {text}\n")
    return "\n".join(parts)


def send_telegram_report(bot, chat_id, reviews):
    """Send a performance report to the given Telegram chat_id using `bot`.

    If `bot` is None, the function will log the formatted report instead (useful
    for testing and environments where a bot object is not available).
    """
    report_text = format_performance_report(chat_id, reviews)
    try:
        if bot:
            # Use send_message for plain text; parse_mode=Markdown for formatting
            bot.send_message(chat_id=chat_id, text=report_text, parse_mode="Markdown")
        else:
            safe_text = mask_secrets(report_text)
            logger.info("Simulated send to %s:\n%s", chat_id, safe_text)
            print(safe_text)
    except Exception as e:
        logger.exception("Failed to send telegram report to %s: %s", chat_id, e)
