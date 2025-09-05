import logging
import os
import sys
from logging.handlers import RotatingFileHandler


def setup_logging():
    """Configures logging for the application."""
    # Create logs directory if it doesn't exist
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # On some Windows/Powershell setups the stdout encoding cannot handle
    # certain Unicode characters (e.g. emojis). If available, reconfigure
    # stdout to use utf-8 so log messages with emoji don't raise UnicodeEncodeError.
    try:
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except Exception:
                # Best-effort; don't fail startup if reconfigure isn't allowed
                pass
    except Exception:
        pass

    # Configure the root logger
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            RotatingFileHandler(
                os.path.join(log_dir, "lunara_bot.log"),
                maxBytes=5 * 1024 * 1024,  # 5 MB
                backupCount=5,
            ),
            # Use a StreamHandler for stdout; after the stdout.reconfigure
            # above this will emit UTF-8 safely on modern Pythons.
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Quieter logging for noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
