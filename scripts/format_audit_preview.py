import sys, os, json
from datetime import datetime
root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
src = os.path.join(root, 'src')
if src not in sys.path:
    sys.path.insert(0, src)
import config, redis

url = os.environ.get('REDIS_URL') or getattr(config, 'REDIS_URL', None)
if not url:
    print('No REDIS_URL')
    sys.exit(1)

r = redis.from_url(url, decode_responses=True)
items = r.lrange('autosuggest_audit', 0, 9) or []
print('Formatting up to', len(items), 'entries')
for raw in items:
    try:
        obj = json.loads(raw)
    except Exception:
        print('RAW:', raw)
        continue
    ts = obj.get('timestamp')
    try:
        ts_dt = datetime.fromisoformat(ts) if ts else None
        ts_fmt = ts_dt.strftime('%Y-%m-%d %H:%M:%S') + ' UTC' if ts_dt else ts
    except Exception:
        ts_fmt = ts
    admin = obj.get('admin_id')
    admin_name = getattr(config, 'ADMIN_ID', str(admin))
    result = obj.get('result')
    created = obj.get('created_trades')
    if isinstance(created, list) and created:
        created_display = ','.join(created)
    else:
        created_display = str(created)
    print(f'- {ts_fmt} by {admin_name} result={result} created={created_display}')
