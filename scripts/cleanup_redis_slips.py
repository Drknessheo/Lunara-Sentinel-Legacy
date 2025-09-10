#!/usr/bin/env python3
"""Safe Redis slip cleanup utility (file import clean).

This version expects to be executed with the repository root on sys.path or
via a helper that injects `src` into sys.path. See `scripts/_run_cleanup.py` for
an easy runner that sets sys.path correctly.
"""

from __future__ import annotations

import argparse
import logging
from typing import List, Tuple


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cleanup unreadable Redis slips")
    parser.add_argument(
        "--action", choices=("dry-run", "quarantine", "purge"), default="dry-run"
    )
    parser.add_argument("--pattern", default="trade:*")
    parser.add_argument("--archive-prefix", default="archive:")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required for purge to actually delete keys",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of keys processed (0 = no limit)",
    )
    args = parser.parse_args(argv)

    try:
        # Import project helpers dynamically; caller should ensure `src` is on sys.path
        import slip_manager as slip_manager
        from logging_utils import mask_secrets
    except Exception as e:  # pragma: no cover - user environment may differ
        print(
            "Failed to import project modules. Run this script from repo root or use scripts/_run_cleanup.py."
        )
        print(e)
        return 2

    logger = logging.getLogger("cleanup_redis_slips")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    client = slip_manager.get_redis_client()
    fernet = slip_manager.get_fernet()
    if fernet is None:
        logger.error(
            "Fernet instance not available. Ensure SLIP_ENCRYPTION_KEY or BINANCE_ENCRYPTION_KEY is set in env."
        )
        return 3

    pattern = args.pattern
    logger.info(f"Scanning Redis for keys matching: {pattern}")

    bad_keys: List[Tuple[str, str]] = []  # (key, error)
    processed = 0

    try:
        for k in client.scan_iter(match=pattern):
            key = k.decode() if isinstance(k, (bytes, bytearray)) else str(k)
            if args.limit and processed >= args.limit:
                break
            processed += 1

            try:
                val = client.get(key)
                if val is None:
                    continue
                if isinstance(val, str):
                    val = val.encode()
                try:
                    fernet.decrypt(val)
                except Exception as de:
                    bad_keys.append((key, str(de)))
            except Exception as e:
                bad_keys.append((key, f"read_error:{e}"))

    except Exception as e:  # pragma: no cover - scan issues depend on Redis connection
        logger.exception("Failed while scanning Redis: %s", e)
        return 4

    logger.info(f"Scanned keys: {processed}, failing decrypts: {len(bad_keys)}")

    if args.action == "dry-run":
        if not bad_keys:
            logger.info("No unreadable slip keys found.")
            return 0
        logger.info("Sample failing keys (first 50):")
        for k, err in bad_keys[:50]:
            print(f" - {k}: {mask_secrets(err)}")
        return 0

    if args.action == "quarantine":
        if not bad_keys:
            logger.info("No unreadable slip keys found, nothing to quarantine.")
            return 0
        logger.info(
            f"Quarantining {len(bad_keys)} keys by copying to prefix '{args.archive_prefix}' and deleting originals."
        )
        for k, _ in bad_keys:
            src_key = k
            dst_key = f"{args.archive_prefix}{k}"
            try:
                val = client.get(src_key)
                if val is None:
                    logger.warning("Key disappeared before quarantine: %s", src_key)
                    continue
                client.set(dst_key, val)
                client.delete(src_key)
                logger.info("Quarantined %s -> %s", src_key, dst_key)
            except Exception as e:
                logger.error("Failed to quarantine %s: %s", src_key, e)
        return 0

    if args.action == "purge":
        if not args.confirm:
            logger.error("Purge requested but --confirm not supplied. Aborting.")
            return 5
        if not bad_keys:
            logger.info("No unreadable slip keys found, nothing to purge.")
            return 0
        logger.info(f"Purging {len(bad_keys)} keys from Redis.")
        for k, _ in bad_keys:
            try:
                client.delete(k)
                logger.info("Deleted %s", k)
            except Exception as e:
                logger.error("Failed to delete %s: %s", k, e)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
