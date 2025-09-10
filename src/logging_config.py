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

    # On Windows/Powershell the console encoding may not support emojis (cp1252).
    # Ensure our StreamHandler writes UTF-8 bytes directly to the underlying
    # buffer to avoid UnicodeEncodeError regardless of sys.stdout's text
    # encoding. This is a best-effort, non-failing change so startup won't be
    # blocked if low-level streams are not modifiable.
    try:
        # Prefer reconfigure when available (Python 3.7+)
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except Exception:
                pass

        # Create a small wrapper that writes UTF-8 bytes to the underlying
        # binary buffer. This avoids Python attempting to encode text using
        # the console encoding (cp1252) which caused the UnicodeEncodeError.
        class _UTF8ByteWriter:
            def __init__(self, buf):
                self._buf = buf

            def write(self, s):
                try:
                    if s is None:
                        return
                    if not isinstance(s, (str, bytes)):
                        s = str(s)
                    if isinstance(s, str):
                        data = s.encode("utf-8", errors="replace")
                    else:
                        data = s
                    # Write raw bytes to the buffer
                    try:
                        self._buf.write(data)
                    except Exception:
                        # Some buffers expect str writes; ignore on failure
                        pass

                except Exception:
                    # Never raise from a logging stream wrapper
                    return

            def flush(self):
                try:
                    self._buf.flush()
                except Exception:
                    pass

        # If the stdout provides a binary buffer, prefer writing bytes to it.
        safe_stream = None
        if hasattr(sys.stdout, "buffer"):
            try:
                safe_stream = _UTF8ByteWriter(sys.stdout.buffer)
            except Exception:
                safe_stream = None
        # Fallback to using sys.stdout directly if no buffer available
        stream_for_handler = safe_stream or sys.stdout
    except Exception:
        # If anything goes wrong configuring the safe stream, fall back to plain stdout
        stream_for_handler = sys.stdout

    # Configure the root logger
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            RotatingFileHandler(
                os.path.join(log_dir, "lunara_bot.log"),
                maxBytes=5 * 1024 * 1024,  # 5 MB
                backupCount=5,
                encoding="utf-8",
            ),
            # Use a StreamHandler that writes UTF-8 bytes where possible to avoid
            # UnicodeEncodeError on Windows consoles that use legacy encodings.
            logging.StreamHandler(stream_for_handler),
        ],
    )

    # Quieter logging for noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
