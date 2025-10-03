"""
Normalize coin_symbol values in the trades table to uppercase.
Usage:
  python scripts/migrate_normalize_symbols.py --db lunara_bot.db [--dry-run] [--apply]

Defaults to dry-run. This script will produce `normalize_symbols_report.csv` and will only perform writes when `--apply` is passed.
It prints a DB backup reminder before any destructive action.
"""
import sqlite3
import argparse
import csv
import os

parser = argparse.ArgumentParser()
parser.add_argument('--db', default='lunara_bot.db')
parser.add_argument('--dry-run', action='store_true', default=True)
parser.add_argument('--apply', action='store_true')
args = parser.parse_args()

if args.apply:
    args.dry_run = False

print('Ensure you have a backup of your DB before applying changes:')
print(f'  cp {args.db} {args.db}.bak')

conn = sqlite3.connect(args.db)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("SELECT id, coin_symbol FROM trades")
rows = cur.fetchall()

changes = []
for r in rows:
    orig = r['coin_symbol']
    if orig is None:
        continue
    up = orig.upper()
    if up != orig:
        changes.append((r['id'], orig, up))

print(f"Found {len(changes)} trades that would be normalized.")
if not changes:
    conn.close()
    exit(0)

with open('normalize_symbols_report.csv', 'w', newline='', encoding='utf-8') as fh:
    writer = csv.writer(fh)
    writer.writerow(['id', 'old', 'new'])
    for c in changes:
        writer.writerow(c)

print('Wrote normalize_symbols_report.csv')

if args.dry_run:
    print('Dry-run mode: no updates applied. Rerun with --apply to perform changes (after backing up DB).')
    conn.close()
    exit(0)

# Apply changes in a transaction
try:
    for id_, old, new in changes:
        cur.execute('UPDATE trades SET coin_symbol = ? WHERE id = ?', (new, id_))
    conn.commit()
    print(f'Applied {len(changes)} updates.')
except Exception as e:
    conn.rollback()
    print('Failed to apply updates:', e)
finally:
    conn.close()

print('Done.')
