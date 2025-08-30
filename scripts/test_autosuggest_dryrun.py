import sys, os, asyncio
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC_DIR = os.path.join(PROJECT_ROOT, 'src')
for p in (PROJECT_ROOT, SRC_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from autotrade_jobs import autotrade_buy_from_suggestions

async def run_test():
    res = await autotrade_buy_from_suggestions(user_id=12345, symbols=['BTCUSDT','ETHUSDT'], context=None, dry_run=True)
    print('DRY RUN RESULT:', res)

if __name__ == '__main__':
    asyncio.run(run_test())
