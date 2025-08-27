
import sqlite3
import os
import sys

# Add project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from src.config import DB_NAME

def migrate_db():
    """Adds the closed_by column to the trades table."""
    try:
        db_path = os.path.join(project_root, DB_NAME)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check if the column already exists
        cursor.execute("PRAGMA table_info(trades)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'closed_by' not in columns:
            print("Adding 'closed_by' column to 'trades' table...")
            cursor.execute('ALTER TABLE trades ADD COLUMN closed_by TEXT')
            conn.commit()
            print("Column 'closed_by' added successfully.")
        else:
            print("Column 'closed_by' already exists.")

    except sqlite3.Error as e:
        print(f"Database error: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    migrate_db()
