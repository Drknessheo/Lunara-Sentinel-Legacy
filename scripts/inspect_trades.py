#!/usr/bin/env python3
"""Inspect the SQLite trades table and highlight missing or NULL fields.

Usage:
  python scripts/inspect_trades.py --db lunara_bot.db --user 6284071528

If --user is omitted the script prints all trades (useful for small DBs).
"""
import argparse
import json
import os
import sqlite3
import sys


def find_trade_table(conn):
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%trade%';"
    )
    rows = cur.fetchall()
    if not rows:
        return None
    # Prefer exact 'trades' if present
    names = [r[0] for r in rows]
    if "trades" in names:
        return "trades"
    return names[0]


def main():
    p = argparse.ArgumentParser(description="Inspect trades table for missing fields")
    p.add_argument(
        "--db",
        default=os.environ.get("DB_PATH", "lunara_bot.db"),
        help="Path to sqlite DB",
    )
    p.add_argument("--user", type=int, help="Filter by user_id")
    p.add_argument("--limit", type=int, default=0, help="Limit results (0 = all)")
    args = p.parse_args()

    if not os.path.exists(args.db):
        print(f"ERROR: DB file not found at {args.db}")
        sys.exit(2)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    table = find_trade_table(conn)
    if not table:
        print("ERROR: No table with 'trade' in the name found in DB. Existing tables:")
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        for r in cur.fetchall():
            print(" -", r[0])
        sys.exit(3)

    print(f"Inspecting table: {table} in {args.db}")

    cur = conn.cursor()
    cols = [c[1] for c in cur.execute(f"PRAGMA table_info({table});")]
    print("Columns:", cols)

    sql = f"SELECT * FROM {table}"
    params = []
    if args.user:
        # try common user column names
        if "user_id" in cols:
            sql += " WHERE user_id = ?"
            params.append(args.user)
        elif "telegram_id" in cols:
            sql += " WHERE telegram_id = ?"
            params.append(args.user)
        else:
            print(
                "Warning: table does not contain a user_id column; returning all rows"
            )
    if args.limit and args.limit > 0:
        sql += f" LIMIT {args.limit}"

    cur.execute(sql, params)
    rows = cur.fetchall()
    if not rows:
        print("No trades found for the given query.")
        return

    problems = 0
    for r in rows:
        rowd = dict(r)
        # Print a compact JSON line for visibility
        print(json.dumps(rowd, default=str))
        # Heuristics: look for common problems
        if "quantity" not in rowd:
            print('>> PROBLEM: "quantity" column missing for this row')
            problems += 1
        else:
            q = rowd.get("quantity")
            if (
                q is None
                or (isinstance(q, (int, float)) and q == 0)
                or (isinstance(q, str) and q.strip() == "")
            ):
                print(
                    ">> PROBLEM: quantity is empty or zero for trade id=",
                    rowd.get("id"),
                )
                problems += 1

    print("\nSummary:")
    print("Total rows inspected:", len(rows))
    print("Problematic rows (missing/empty quantity):", problems)


if __name__ == "__main__":
    main()
