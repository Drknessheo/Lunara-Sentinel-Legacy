import sys, os, asyncio
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC_DIR = os.path.join(PROJECT_ROOT, 'src')
for p in (PROJECT_ROOT, SRC_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import importlib

try:
    _mod = importlib.import_module('src.autotrade_jobs')
except Exception:
    _mod = importlib.import_module('autotrade_jobs')

autotrade_buy_from_suggestions = getattr(_mod, 'autotrade_buy_from_suggestions')
import config

async def run():
    symbols = ['BTCUSDT', 'ADAUSDT', 'ETHUSDT']
    print('Running force-create for symbols:', symbols)
    created = await autotrade_buy_from_suggestions(config.ADMIN_USER_ID, symbols=symbols, context=None, dry_run=False, max_create=3)
    print('CREATED:', created)

if __name__ == '__main__':
    asyncio.run(run())
