# db_setup.py
"""
Database setup and migration script for Lunara Bot.
Run this file to initialize and migrate the database schema.
"""

import logging

try:
    from src.modules import db_access
except ImportError:
    raise ImportError("Could not import 'db' module. Make sure 'db.py' is present in your project.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.info("Initializing database...")
    db_access.initialize_database()
    logging.info("Running schema migrations...")
    db_access.migrate_schema()
    logging.info("Database setup and migration complete.")