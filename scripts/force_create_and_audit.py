import sys, os, asyncio, json
from datetime import datetime
# Ensure src is importable
root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
src = os.path.join(root, 'src')
if src not in sys.path:
    sys.path.insert(0, src)

import config, redis
from autotrade_jobs import force_create_mock_slips

async def main():
    user_id = getattr(config, 'ADMIN_USER_ID', None)
    if not user_id:
        print('ADMIN_USER_ID not set; aborting')
        return

    symbols = ['BTCUSDT', 'ETHUSDT']
    print('Forcing creation for symbols:', symbols)
    created = await force_create_mock_slips(user_id=user_id, symbols=symbols, context=None, max_create=2)
    print('Created slips:', created)

    # Write audit entry
    url = os.environ.get('REDIS_URL') or getattr(config, 'REDIS_URL', None)
    if not url:
        print('No REDIS_URL; skipping audit write')
        return
    r = redis.from_url(url, decode_responses=True)
    audit_entry = {
        'admin_id': user_id,
        'action': 'force_create_test',
        'max_create': 2,
        'timestamp': datetime.utcnow().isoformat(),
        'message_id': None,
        'created_trades': created,
        'result': 'created' if created else 'no_created'
    }
    r.lpush('autosuggest_audit', json.dumps(audit_entry))
    r.set('autosuggest:last', json.dumps(audit_entry))

    # Print raw JSON
    print('\nRAW AUDIT JSON:')
    print(json.dumps(audit_entry, indent=2))

    # Formatted preview
    print('\nFORMATTED PREVIEW:')
    ts = audit_entry.get('timestamp')
    try:
        ts_fmt = datetime.fromisoformat(ts).strftime('%Y-%m-%d %H:%M:%S') + ' UTC'
    except Exception:
        ts_fmt = ts
    admin_name = getattr(config, 'ADMIN_ID', str(user_id))
    created_display = ','.join(created) if isinstance(created, list) and created else str(created)
    print(f'- {ts_fmt} by {admin_name} result={audit_entry.get("result")} created={created_display}')

if __name__ == '__main__':
    asyncio.run(main())
