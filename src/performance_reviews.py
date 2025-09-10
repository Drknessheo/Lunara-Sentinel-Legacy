import os
import sqlite3
import time
from typing import Dict, List

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "reviews.db")


def _ensure_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            rating INTEGER NOT NULL,
            review_text TEXT
        )
        """
    )
    conn.commit()
    return conn


def add_review(user_id: str, rating: int, notes: str) -> int:
    """Add a performance review; returns inserted row id."""
    conn = _ensure_db()
    cur = conn.cursor()
    ts = int(time.time())
    cur.execute(
        "INSERT INTO reviews (user_id, timestamp, rating, review_text) VALUES (?, ?, ?, ?)",
        (str(user_id), ts, int(rating), notes or ""),
    )
    conn.commit()
    rowid = cur.lastrowid
    conn.close()
    return rowid


def get_reviews(user_id: str) -> List[Dict]:
    conn = _ensure_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, timestamp, rating, review_text FROM reviews WHERE user_id = ? ORDER BY timestamp DESC",
        (str(user_id),),
    )
    rows = cur.fetchall()
    conn.close()
    return [
        {"id": r[0], "timestamp": r[1], "rating": r[2], "review_text": r[3]}
        for r in rows
    ]


def recycle_old_reviews(user_id: str, older_than_days: int = 365) -> int:
    """Delete reviews older than `older_than_days` for the user and return number deleted."""
    cutoff = int(time.time()) - int(older_than_days) * 24 * 3600
    conn = _ensure_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(1) FROM reviews WHERE user_id = ? AND timestamp < ?",
        (str(user_id), cutoff),
    )
    cnt = cur.fetchone()[0]
    cur.execute(
        "DELETE FROM reviews WHERE user_id = ? AND timestamp < ?",
        (str(user_id), cutoff),
    )
    conn.commit()
    conn.close()
    return int(cnt)


def clear_reviews(user_id: str) -> int:
    """Delete all reviews for a user; return deleted count."""
    conn = _ensure_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(1) FROM reviews WHERE user_id = ?", (str(user_id),))
    cnt = cur.fetchone()[0]
    cur.execute("DELETE FROM reviews WHERE user_id = ?", (str(user_id),))
    conn.commit()
    conn.close()
    return int(cnt)
