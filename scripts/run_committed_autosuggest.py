import sys, os, asyncio
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC_DIR = os.path.join(PROJECT_ROOT, 'src')
for p in (PROJECT_ROOT, SRC_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import config
from autotrade_jobs import autotrade_buy_from_suggestions

async def run_test():
    print(f"Using ADMIN_USER_ID={config.ADMIN_USER_ID}")
    created = await autotrade_buy_from_suggestions(config.ADMIN_USER_ID, symbols=None, context=None, dry_run=False, max_create=3)
    print('CREATED:', created)

if __name__ == '__main__':
    asyncio.run(run_test())
