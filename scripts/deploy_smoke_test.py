#!/usr/bin/env python3
"""Quick deploy smoke-test for Lunara bot.

Checks (fast, local):
 - Python imports for key modules
 - `Lunessa_db.initialize_database()` runs without error
 - Redis connectivity (if REDIS_URL or config.REDIS_URL available)
 - Presence of `autosuggest_audit` list (peek few items)

Run from repository root with: python .\scripts\deploy_smoke_test.py
"""
import os
import sys
import json
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC = os.path.join(ROOT, 'src')
sys.path.insert(0, SRC)
sys.path.insert(0, ROOT)

OK = []
ERR = []

def note_ok(msg):
    OK.append(msg)
    print('[OK] ', msg)

def note_err(msg):
    ERR.append(msg)
    print('[ERR]', msg)

def import_check(name):
    try:
        __import__(name)
        note_ok(f'import {name}')
        return True
    except Exception as e:
        note_err(f'import {name} failed: {e.__class__.__name__}: {e}')
        return False

def main():
    print('Lunara deploy smoke-test')

    # 1) imports
    import_check('src.Lunessa_db')

    # 2) run initialize_database
    try:
        try:
            from src import Lunessa_db as LDB
        except Exception:
            import Lunessa_db as LDB
        LDB.initialize_database()
        note_ok('initialize_database() executed')
    except Exception as e:
        note_err('initialize_database() failed: ' + repr(e))
        traceback.print_exc()

    # 3) optional Redis checks
    REDIS_URL = None
    try:
        import config
        REDIS_URL = getattr(config, 'REDIS_URL', None)
    except Exception:
        pass

    if not REDIS_URL:
        REDIS_URL = os.environ.get('REDIS_URL')

    if not REDIS_URL:
        # Allow CI to skip Redis checks by setting SKIP_REDIS=1 in the environment.
        if os.environ.get('SKIP_REDIS') == '1':
            note_ok('SKIP_REDIS=1 set; skipping Redis checks')
        else:
            note_err('REDIS_URL not found in config or env; skipping Redis checks')
    else:
        try:
            import redis
            r = redis.from_url(REDIS_URL, socket_connect_timeout=5, decode_responses=True)
            pong = r.ping()
            note_ok(f'Redis ping -> {pong}')

            # peek the autosuggest audit list
            try:
                audit = r.lrange('autosuggest_audit', 0, 4)
                note_ok(f'autosuggest_audit length peek={len(audit)}')
                if audit:
                    print('Sample audit item (parsed):')
                    try:
                        print(json.dumps(json.loads(audit[0]), indent=2))
                    except Exception:
                        print('  (non-json sample) ', audit[0][:200])
            except Exception as e:
                note_err('failed to read autosuggest_audit: ' + repr(e))

        except Exception as e:
            note_err('Redis connection failed: ' + repr(e))

    # 4) optional Binance SDK import + ping check (skip with SKIP_BINANCE=1)
    if os.environ.get('SKIP_BINANCE') == '1':
        note_ok('SKIP_BINANCE=1 set; skipping Binance checks')
    else:
        try:
            from binance.client import Client
            # Create a client without credentials for a lightweight ping; if the environment
            # provides BINANCE_API_KEY/SECRET, Client() will use them; otherwise this still tests
            # that the module and Client class are importable and ping is callable.
            try:
                c = Client()
                # ping should be callable; wrap in try/except in case network or auth blocks it.
                try:
                    resp = c.ping()
                    note_ok('binance.Client.ping() callable')
                except Exception as e:
                    # ping may fail due to network/auth; still treat import as success but report ping error.
                    note_err('binance ping failed: ' + repr(e))
            except Exception as e:
                note_err('failed to instantiate binance.Client: ' + repr(e))
        except Exception as e:
            note_err('import binance.client failed: ' + repr(e))

    # Summary
    print('\nSummary:')
    for s in OK:
        print('  OK -', s)
    for s in ERR:
        print('  ERR -', s)

    if ERR:
        print('\nSmoke-test encountered errors. Fix before deploying.')
        sys.exit(2)
    print('\nSmoke-test passed. Ready to continue with deployment smoke checks.')

if __name__ == '__main__':
    main()
