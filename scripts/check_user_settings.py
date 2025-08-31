#!/usr/bin/env python3
"""Scan Redis for keys related to a user and display their values.

Usage:
  Set REDIS_URL in the environment (or pass --redis), then:
  python scripts/check_user_settings.py --user 6284071528

This tool uses SCAN to avoid blocking large instances. It will attempt to fetch common types.
"""
import os
import argparse
import json

try:
    import redis
except Exception as e:
    print('ERROR: redis package not available. Install with: pip install redis')
    raise


def fetch_value(r, key, max_list=50):
    t = r.type(key)
    try:
        if t == b'string' or t == 'string':
            return r.get(key)
        if t == b'hash' or t == 'hash':
            return r.hgetall(key)
        if t == b'list' or t == 'list':
            return r.lrange(key, 0, max_list-1)
        if t == b'set' or t == 'set':
            return list(r.smembers(key))
        if t == b'zset' or t == 'zset':
            return r.zrange(key, 0, max_list-1, withscores=True)
        return f'<unknown-type:{t}>'
    except Exception as e:
        return f'<error reading key: {e}>'


def main():
    p = argparse.ArgumentParser(description='Inspect Redis keys for a specific user')
    p.add_argument('--redis', default=os.environ.get('REDIS_URL'), help='Redis URL (env REDIS_URL)')
    p.add_argument('--user', required=True, help='User id substring to search for')
    p.add_argument('--pattern', help='Custom key pattern to scan (overrides user)')
    p.add_argument('--max', type=int, default=200, help='Max keys to report')
    args = p.parse_args()

    if not args.redis:
        print('ERROR: Redis URL not provided. Set REDIS_URL or pass --redis')
        return

    r = redis.from_url(args.redis, decode_responses=True)

    pattern = args.pattern or f'*{args.user}*'
    print('Scanning Redis for pattern:', pattern)

    found = 0
    try:
        for key in r.scan_iter(match=pattern, count=100):
            if found >= args.max:
                break
            val = fetch_value(r, key)
            print('KEY:', key)
            try:
                print(json.dumps(val, default=str, ensure_ascii=False))
            except Exception:
                print(str(val))
            print('---')
            found += 1
    except Exception as e:
        print('Scan failed:', e)
        return

    if found == 0:
        print('No keys matched the pattern. Consider broadening the pattern or checking DB-backed settings.')
    else:
        print(f'Found {found} keys (showing up to {args.max}).')


if __name__ == '__main__':
    main()
