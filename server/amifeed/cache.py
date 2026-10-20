"""A size capped key/value cache with expiry, in sqlite (survives restarts)."""

import sqlite3
import threading
import time


class Cache:
    def __init__(self, path, ttl, max_bytes):
        self.ttl, self.max_bytes = ttl, max_bytes
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS cache "
                        "(key TEXT PRIMARY KEY, value BLOB, stored REAL)")
        self.db.commit()

    def get(self, key):
        with self.lock:
            row = self.db.execute("SELECT value, stored FROM cache WHERE key = ?", (key,)).fetchone()
        if row and time.time() - row[1] < self.ttl:
            return row[0]
        return None

    def put(self, key, value):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (key, value, time.time()))
            self.db.execute("DELETE FROM cache WHERE stored < ?", (time.time() - self.ttl,))
            total = self.db.execute("SELECT COALESCE(SUM(LENGTH(value)), 0) FROM cache").fetchone()[0]
            if total > self.max_bytes:  # drop the oldest until under the cap
                for key_, size in self.db.execute(
                        "SELECT key, LENGTH(value) FROM cache ORDER BY stored").fetchall():
                    if total <= self.max_bytes * 0.9:
                        break
                    self.db.execute("DELETE FROM cache WHERE key = ?", (key_,))
                    total -= size
            self.db.commit()
