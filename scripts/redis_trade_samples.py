import os
import sys
import json
import redis

# Ensure src/ is importable
root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
src_path = os.path.join(root, 'src')
if src_path not in sys.path:
    sys.path.insert(0, src_path)

import config


def classify_value(value):
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return 'JSON', parsed
        return 'JSON_OTHER', parsed
    except Exception:
        try:
            f = float(value)
            return 'NUMERIC', f
        except Exception:
            return 'RAW', value


def main():
    url = os.environ.get('REDIS_URL') or getattr(config, 'REDIS_URL', None)
    if not url:
        print('No REDIS_URL found in env or config')
        return
    r = redis.from_url(url)
    it = r.scan_iter('trade:*')
    print('Using Redis URL:', url)
    print('\nSample trade keys (up to 20):')
    for i, key in enumerate(it):
        if i >= 20:
            break
        try:
            k = key.decode() if isinstance(key, bytes) else str(key)
            v = r.get(key)
            if v is None:
                print(f'- {k}: (nil)')
                continue
            try:
                s = v.decode('utf-8')
            except Exception:
                s = repr(v)
            kind, parsed = classify_value(s)
            print(f'- {k} [{kind}] -> {parsed}')
        except Exception as exc:
            print(f'- ERROR reading key {key}: {exc}')


if __name__ == '__main__':
    main()
