import asyncio
import os
import sys

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)

try:
    from src.autotrade_jobs import monitor_autotrades
except Exception:
    import importlib

    try:
        _mod = importlib.import_module("src.autotrade_jobs")
    except Exception:
        _mod = importlib.import_module("autotrade_jobs")

    monitor_autotrades = getattr(_mod, "monitor_autotrades")


async def main(dry_run=True):
    print("[MANUALMONITOR] Starting manual monitor runner (dry_run=%s)" % dry_run)
    await monitor_autotrades(context=None, dry_run=dry_run)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", dest="dry_run", action="store_true")
    p.add_argument("--live", dest="dry_run", action="store_false")
    p.set_defaults(dry_run=True)
    args = p.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
