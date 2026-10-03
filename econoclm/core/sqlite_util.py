"""The one SQLite connection pattern both stores use.

WAL mode (readers never block the writer), a busy timeout (a second process waits
instead of failing), and one lock per process object (one connection shared by
threads, used one at a time).
"""

import sqlite3
import threading
from pathlib import Path


class Store:
    def __init__(self, path: str | Path, schema: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: we write BEGIN/COMMIT ourselves where it matters.
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None,
                                    timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=30000")
            self.conn.executescript(schema)

    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def execute(self, sql: str, args: tuple = ()) -> None:
        with self.lock:
            self.conn.execute(sql, args)

    def close(self) -> None:
        with self.lock:
            self.conn.close()
