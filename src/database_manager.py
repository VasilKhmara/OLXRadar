import logging
import os
import sqlite3

from utils import BASE_DIR


class DatabaseManager:
    """Thread-safe helper around the ads SQLite database."""

    def __init__(self) -> None:
        self.db_path = os.path.join(BASE_DIR, "../database.db")
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        """
        Create a brand-new connection for each call.

        Using short-lived connections avoids the default sqlite restriction
        about accessing the same connection from different threads.
        """
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _ensure_schema(self) -> None:
        sql_create_table = """
            CREATE TABLE IF NOT EXISTS ads (
                id          INTEGER     PRIMARY KEY     AUTOINCREMENT,
                url         TEXT        NOT NULL UNIQUE
            );
        """
        with self._connect() as conn:
            conn.execute(sql_create_table)

    def url_exists(self, url: str) -> bool:
        """
        Returns True if an entry with the specified url exists
        in the database, otherwise False.
        """
        query = "SELECT 1 FROM ads WHERE url = ? LIMIT 1"
        with self._connect() as conn:
            cursor = conn.execute(query, (url,))
            exists = cursor.fetchone() is not None
        logging.debug(f"[db] url_exists -> {exists} for {url}")
        return exists

    def add_url(self, url: str) -> None:
        """Adds a new entry with the specified url to the 'ads' table."""
        sql = "INSERT OR IGNORE INTO ads (url) VALUES (?)"
        with self._connect() as conn:
            conn.execute(sql, (url,))
