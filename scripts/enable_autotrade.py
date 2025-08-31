#!/usr/bin/env python3
"""One-off script to enable autotrade for ADMIN_USER_ID.

This version uses a safe dynamic import strategy so editors/linters
don't flag missing top-level imports, and runtime picks the first
available DB helper module.

Usage:
  python scripts/enable_autotrade.py
"""
import os
import sys
import logging

logging.basicConfig(level=logging.INFO)

# Dynamic imports for your DB layer
db = None
for module in ("src.db", "src.Lunessa_db", "src.modules.db_access"):
    try:
        __import__(module)
        db = sys.modules[module]
        break
    except ImportError:
        continue

if db is None:
    logging.error("No DB helper module found. Exiting.")
    sys.exit(1)

def main():
    ADMIN_ID = int(os.environ.get("ADMIN_USER_ID", 0) or 0)
    if not ADMIN_ID:
        logging.error("ADMIN_USER_ID not set")
        sys.exit(1)

    try:
        # assume each module has set_autotrade or set_autotrade_status
        setter = getattr(db, "set_autotrade", None) or getattr(db, "set_autotrade_status", None)
        if setter is None:
            raise AttributeError("No setter in %s" % db.__name__)
        setter(ADMIN_ID, True)
        print(f"✅ Autotrade enabled for admin {ADMIN_ID}")
    except Exception as e:
        logging.exception("Failed to enable autotrade:")
        sys.exit(1)

if __name__ == "__main__":
    main()
