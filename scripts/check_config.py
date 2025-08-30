import sys, os
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config

print('PROJECT_ROOT=', PROJECT_ROOT)
print('REDIS_URL:', getattr(config, 'REDIS_URL', None))
print('BINANCE_ENCRYPTION_KEY present:', bool(getattr(config, 'BINANCE_ENCRYPTION_KEY', None)))
print('SANDPAPER_ENCRYPTION_KEY present:', bool(getattr(config, 'SANDPAPER_ENCRYPTION_KEY', None)))
print('SLIP_ENCRYPTION_KEY:', getattr(config, 'SLIP_ENCRYPTION_KEY', None))
print('ADMIN_USER_ID:', getattr(config, 'ADMIN_USER_ID', None))
print('\nAVAILABLE UPPERCASE NAMES (sample):', [n for n in dir(config) if n.isupper()][:80])
