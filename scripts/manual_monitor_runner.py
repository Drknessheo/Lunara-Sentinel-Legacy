import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from autotrade_jobs import monitor_autotrades


async def main(dry_run=True):
    print('[MANUALMONITOR] Starting manual monitor runner (dry_run=%s)' % dry_run)
    await monitor_autotrades(context=None, dry_run=dry_run)


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', dest='dry_run', action='store_true')
    p.add_argument('--live', dest='dry_run', action='store_false')
    p.set_defaults(dry_run=True)
    args = p.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
