import json
import sys
import traceback
from cryptography.fernet import Fernet, InvalidToken
import redis
import os

# Ensure project root (parent of scripts/) is on sys.path so `import config` succeeds
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config


def make_fernet_from_env(key_bytes):
    if not key_bytes:
        return None
    try:
        if isinstance(key_bytes, str):
            key_bytes = key_bytes.encode()
        return Fernet(key_bytes)
    except Exception:
        return None


def try_decrypt(val, fernet):
    if fernet is None:
        return None
    try:
        if isinstance(val, str):
            val = val.encode()
        dec = fernet.decrypt(val)
        try:
            return dec.decode()
        except Exception:
            return dec
    except InvalidToken:
        return None
    except Exception:
        return None


def main():
    print('[DIAG] Loading REDIS_URL from config:', bool(getattr(config, 'REDIS_URL', None)))
    try:
        r = redis.from_url(config.REDIS_URL)
    except Exception as e:
        print('[DIAG] Could not create redis client:', e)
        return

    # Candidate keys to try
    candidates = {
        'BINANCE_ENCRYPTION_KEY': getattr(config, 'BINANCE_ENCRYPTION_KEY', None),
        'SANDPAPER_ENCRYPTION_KEY': getattr(config, 'SANDPAPER_ENCRYPTION_KEY', None),
        'SLIP_ENCRYPTION_KEY': getattr(config, 'SLIP_ENCRYPTION_KEY', None),
    }

    fernets = {name: make_fernet_from_env(val) for name, val in candidates.items()}

    # Collect a sample of trade:* keys
    try:
        sample = []
        for k in r.scan_iter(match='trade:*', count=100):
            sample.append(k)
            if len(sample) >= 20:
                break
    except Exception as e:
        print('[DIAG] Error scanning redis keys:', e)
        return

    if not sample:
        print('[DIAG] No trade:* keys found in Redis.')
        return

    print(f'[DIAG] Found {len(sample)} trade:* keys; trying decryption with candidates...')

    for k in sample:
        try:
            raw = r.get(k)
        except Exception as e:
            print('[DIAG] Error getting key', k, e)
            continue
        print('\n[KEY]', k)
        if raw is None:
            print('  - value: <nil>')
            continue
        # Show prefix
        try:
            prefix = raw[:6].decode(errors='ignore')
        except Exception:
            prefix = str(raw[:6])
        print('  - raw prefix:', prefix)
        # Try each fernet
        for name, f in fernets.items():
            if f is None:
                print(f'    - {name}: <not configured>')
                continue
            try:
                dec = try_decrypt(raw, f)
                if dec is None:
                    print(f'    - {name}: INVALID')
                else:
                    print(f'    - {name}: SUCCESS ->', repr(dec[:200]))
            except Exception as e:
                print(f'    - {name}: exception {e}')
        # Also try to interpret as utf-8/json
        try:
            txt = raw.decode()
            try:
                parsed = json.loads(txt)
                print('  - as JSON ->', json.dumps(parsed, indent=2)[:300])
            except Exception:
                print('  - as UTF-8 ->', txt[:300])
        except Exception:
            print('  - raw bytes (non-decodable)')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(2)
