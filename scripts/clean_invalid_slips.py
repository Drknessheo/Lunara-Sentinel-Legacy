#!/usr/bin/env python3
"""List and optionally delete invalid trade slips from Redis.

Usage:
  python .\scripts\clean_invalid_slips.py         # list invalid slips
  python .\scripts\clean_invalid_slips.py --force # delete invalid slips

The script looks for trade keys where the decrypted slip lacks a 'symbol' field
or where per-field keys exist without reconstructable data.
"""
import os
import sys
import json
import argparse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC = os.path.join(ROOT, 'src')
sys.path.insert(0, SRC)
sys.path.insert(0, ROOT)

try:
    import importlib

    try:
        _mod = importlib.import_module('src.slip_manager')
    except Exception:
        _mod = importlib.import_module('slip_manager')

    list_all_slips = getattr(_mod, 'list_all_slips')
    delete_slip = getattr(_mod, 'delete_slip')
except Exception:
    # As a final fallback, try to import by plain module name via importlib
    try:
        _mod = importlib.import_module('slip_manager')
        list_all_slips = getattr(_mod, 'list_all_slips')
        delete_slip = getattr(_mod, 'delete_slip')
    except Exception:
        raise

parser = argparse.ArgumentParser()
parser.add_argument('--force', action='store_true', help='Delete invalid slips')
args = parser.parse_args()

invalid = []
all_slips = list_all_slips()
for s in all_slips:
    data = s.get('data')
    if not isinstance(data, dict) or 'symbol' not in data:
        invalid.append(s)

if not invalid:
    print('No invalid slips found.')
    sys.exit(0)

print('Invalid slips found:')
for s in invalid:
    print('-', s['key'], '=>', json.dumps(s.get('data'))) 

if args.force:
    confirm = input('Are you sure you want to DELETE these keys from Redis? Type YES to confirm: ')
    if confirm == 'YES':
        for s in invalid:
            print('Deleting', s['key'])
            delete_slip(s['key'])
        print('Deletion complete.')
    else:
        print('Aborted by user.')
else:
    print('\nRun with --force to delete these keys from Redis.')
