import sys, os, asyncio
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC_DIR = os.path.join(PROJECT_ROOT, 'src')
for p in (PROJECT_ROOT, SRC_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from autotrade_jobs import force_create_mock_slips
import config

async def run():
    symbols = ['BTCUSDT', 'ADAUSDT', 'ETHUSDT']
    print('Forcing creation for symbols:', symbols)
    created = await force_create_mock_slips(config.ADMIN_USER_ID, symbols, context=None, max_create=3)
    print('CREATED:', created)

if __name__ == '__main__':
    asyncio.run(run())
