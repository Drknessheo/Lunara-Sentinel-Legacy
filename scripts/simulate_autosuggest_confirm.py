import sys
import os
import asyncio
import json
from datetime import datetime

# Ensure src is importable
root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
src_path = os.path.join(root, 'src')
if src_path not in sys.path:
    sys.path.insert(0, src_path)

import config
import redis
from autotrade_jobs import autotrade_buy_from_suggestions

async def main():
    user_id = getattr(config, 'ADMIN_USER_ID', None)
    if not user_id:
        print('No ADMIN_USER_ID configured; aborting')
        return

    max_create = 3
    print(f"Running autotrade_buy_from_suggestions(dry_run=False, max_create={max_create}) as user {user_id}")
    created = []
    try:
        created = await autotrade_buy_from_suggestions(user_id=user_id, symbols=['BTCUSDT','ADAUSDT','ETHUSDT'], context=None, dry_run=False, max_create=max_create)
        print('Created:', created)
    except Exception as e:
        print('Error during creation:', e)

    # Write audit entry to Redis
    url = os.environ.get('REDIS_URL') or getattr(config, 'REDIS_URL', None)
    if not url:
        print('No REDIS_URL available, skipping audit write')
        return
    try:
        r = redis.from_url(url, decode_responses=True)
        audit_entry = {
            'admin_id': user_id,
            'action': 'autosuggest_commit',
            'max_create': max_create,
            'timestamp': datetime.utcnow().isoformat(),
            'message_id': None,
            'created_trades': created,
            'result': 'ok' if created else 'no_created'
        }
        r.lpush('autosuggest_audit', json.dumps(audit_entry))
        r.set('autosuggest:last', json.dumps(audit_entry))
        print('Wrote audit entry to Redis')
        print('Latest audit (LRANGE 0 4):', r.lrange('autosuggest_audit', 0, 4))
        print('autosuggest:last ->', r.get('autosuggest:last'))
    except Exception as e:
        print('Failed to write/read audit from Redis:', e)

if __name__ == '__main__':
    asyncio.run(main())
