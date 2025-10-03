"""
Backfill script to fix trades with NULL or zero quantities.
Usage:
  python scripts/backfill_trade_quantities.py --db lunara_bot.db [--dry-run] [--placeholder 0.001]

This script will:
- Scan `trades` for rows where `quantity IS NULL OR quantity = 0`.
- Optionally update those rows with a provided placeholder quantity.
- Export a CSV `backfill_report.csv` listing affected rows for manual review.

Run in a safe environment and backup your DB before applying changes.
"""
import sqlite3
import argparse
import csv
import time

parser = argparse.ArgumentParser()
parser.add_argument('--db', default='lunara_bot.db')
parser.add_argument('--dry-run', action='store_true')
parser.add_argument('--placeholder', type=float, default=0.001)
parser.add_argument('--estimate', action='store_true', help='Estimate quantities using trade_size_usdt / buy_price and write to audit table (dry-run by default)')
parser.add_argument('--apply', action='store_true', help='Apply changes to DB (required for writes)')
args = parser.parse_args()

if args.apply:
    args.dry_run = False

conn = sqlite3.connect(args.db)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("SELECT * FROM trades WHERE quantity IS NULL OR quantity = 0")
rows = cur.fetchall()

print(f"Found {len(rows)} problematic trades.")
if not rows:
    conn.close()
    exit(0)

report_rows = []
for r in rows:
    report_rows.append([r['id'], r['user_id'], r['coin_symbol'], r['buy_price'], r['quantity'], r['buy_timestamp']])

with open('backfill_report.csv', 'w', newline='', encoding='utf-8') as fh:
    writer = csv.writer(fh)
    writer.writerow(['id', 'user_id', 'coin_symbol', 'buy_price', 'quantity', 'buy_timestamp'])
    writer.writerows(report_rows)

print('Wrote backfill_report.csv')

if args.estimate:
    # Prepare audit table
    cur.execute('''CREATE TABLE IF NOT EXISTS estimated_quantities_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER NOT NULL,
        estimated_quantity REAL NOT NULL,
        source_price REAL,
        source_trade_size_usdt REAL,
        confidence REAL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    estimates = []
    for r in rows:
        trade_id = r['id']
        price = r['buy_price']
        trade_size = r['trade_size_usdt'] if 'trade_size_usdt' in r.keys() else None
        if price and trade_size:
            try:
                est_qty = round(float(trade_size) / float(price), 6)
                confidence = 0.9
                estimates.append((trade_id, est_qty, price, trade_size, confidence))
            except Exception:
                estimates.append((trade_id, None, price, trade_size, 0.0))
        else:
            # Log skipped rows
            estimates.append((trade_id, None, price, trade_size, 0.0))

    # Write CSV of estimates
    with open('estimated_quantities.csv', 'w', newline='', encoding='utf-8') as fh:
        writer = csv.writer(fh)
        writer.writerow(['trade_id', 'estimated_quantity', 'source_price', 'source_trade_size_usdt', 'confidence'])
        for e in estimates:
            writer.writerow(e)

    print('Wrote estimated_quantities.csv')

    if args.dry_run:
        print('Dry-run mode: no audit rows written to DB. Rerun with --apply to persist estimates (after backing up DB).')
        conn.close()
        exit(0)

    # Persist estimates transactionally
    try:
        for trade_id, est_qty, price, trade_size, confidence in estimates:
            if est_qty is None:
                continue
            cur.execute('INSERT INTO estimated_quantities_audit (trade_id, estimated_quantity, source_price, source_trade_size_usdt, confidence) VALUES (?, ?, ?, ?, ?)',
                        (trade_id, est_qty, price, trade_size, confidence))
        conn.commit()
        print(f'Inserted {len([e for e in estimates if e[1] is not None])} audit rows into estimated_quantities_audit')
    except Exception as e:
        conn.rollback()
        print('Failed to persist estimates:', e)
    finally:
        conn.close()
    print('Done.')

else:
    # Existing placeholder update path
    if args.dry_run:
        print('Dry run complete, no updates applied.')
        conn.close()
        exit(0)

    print(f"Updating {len(rows)} rows to placeholder quantity={args.placeholder}...")
    updated = 0
    for r in rows:
        try:
            cur.execute("UPDATE trades SET quantity = ? WHERE id = ?", (args.placeholder, r['id']))
            updated += 1
        except Exception as e:
            print(f"Failed to update id={r['id']}: {e}")

    conn.commit()
    conn.close()
    print(f"Updated {updated} rows.")
    print('Backup your DB and review backfill_report.csv for accuracy.')
