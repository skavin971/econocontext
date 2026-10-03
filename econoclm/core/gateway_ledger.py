"""The gateway's ledger: one row per model call, in runs/<date>/gateway.sqlite.

Written only by the gateway process. Agents and analysis scripts read it.

call_no is assigned INSIDE the insert's write transaction (BEGIN IMMEDIATE takes the
write lock first), so concurrent calls of one run can never get the same number.
(The feature/claude-code gateway counted before inserting, which duplicated numbers.)

Rate-limit columns (so rate limits never skew the comparison):
  upstream_attempts   how many times the gateway sent this call upstream (1 = no retry)
  ratelimit_wait_ms   time spent backing off after upstream 429/503 replies
  queue_ms            time waiting for a free in-flight slot in the gateway
  latency_ms          the final upstream attempt only (excludes the two waits above)

usage_anomaly = 1: the usage numbers don't add up, or a 200 reply had no usable output
count (core/usage.py). Older ledgers get the column when opened.
"""

import time
from pathlib import Path

from .sqlite_util import Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
  run_id TEXT NOT NULL,
  call_no INTEGER NOT NULL,
  ts REAL NOT NULL,
  prompt_tokens INTEGER,
  cached_tokens INTEGER,
  uncached_tokens INTEGER,
  output_tokens INTEGER,
  reasoning_tokens INTEGER,
  cost_usd REAL,
  latency_ms REAL,
  finish_reason TEXT,
  http_status INTEGER,
  upstream_attempts INTEGER,
  ratelimit_wait_ms REAL,
  queue_ms REAL,
  model TEXT,
  stream INTEGER,
  cached_reported INTEGER,      -- 1 if prompt_tokens_details was present
  usage_anomaly INTEGER,        -- 1 if the usage numbers are inconsistent or missing
  PRIMARY KEY (run_id, call_no)
);
"""

COLUMNS = ("prompt_tokens", "cached_tokens", "uncached_tokens", "output_tokens",
           "reasoning_tokens", "cost_usd", "latency_ms", "finish_reason", "http_status",
           "upstream_attempts", "ratelimit_wait_ms", "queue_ms", "model", "stream",
           "cached_reported", "usage_anomaly")


class Ledger(Store):
    def __init__(self, path: str | Path):
        super().__init__(path, SCHEMA)
        have = {r["name"] for r in self.rows("PRAGMA table_info(calls)")}
        if "usage_anomaly" not in have:
            self.execute("ALTER TABLE calls ADD COLUMN usage_anomaly INTEGER")

    def insert(self, run_id: str, **fields) -> int:
        """Record one call; returns its call_no (0, 1, 2, ... per run)."""
        unknown = set(fields) - set(COLUMNS)
        if unknown:
            raise ValueError(f"unknown ledger columns: {sorted(unknown)}")
        cols = ["run_id", "call_no", "ts", *fields]
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                call_no = self.conn.execute(
                    "SELECT COALESCE(MAX(call_no), -1) + 1 FROM calls WHERE run_id=?",
                    (run_id,)).fetchone()[0]
                self.conn.execute(
                    f"INSERT INTO calls ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    (run_id, call_no, time.time(), *fields.values()))
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
        return call_no

    def total_spend(self) -> float:
        return self.rows("SELECT COALESCE(SUM(cost_usd), 0) s FROM calls")[0]["s"]

    def run_spend(self, run_id: str) -> float:
        return self.rows("SELECT COALESCE(SUM(cost_usd), 0) s FROM calls WHERE run_id=?",
                         (run_id,))[0]["s"]

    def latest(self, run_id: str) -> dict | None:
        found = self.rows("SELECT * FROM calls WHERE run_id=? ORDER BY call_no DESC LIMIT 1",
                          (run_id,))
        return found[0] if found else None

    def calls(self, run_id: str) -> list[dict]:
        return self.rows("SELECT * FROM calls WHERE run_id=? ORDER BY call_no", (run_id,))
