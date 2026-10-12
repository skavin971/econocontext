"""PostgresAgentDB: one agent's context on Postgres, through named operations (CHANGES.md lists what was
trimmed from the upstream version).

Operations
- runs: start_run, get_run
- segments: add_segment (write-once), segment
- the window: set_window (atomic; each segment carries its position, part and form), window,
  window_results (the tool results shown in the window, with what they were called with)
- tool results and versions: add_tool_result, find_tool_result, current_versions, bump, invalidate

Conventions
- Every operation takes the one lock; host callbacks may arrive on several threads.
- Writes that must be atomic (set_window, bump) run in one transaction.
- Rows come back as dicts; timestamptz values as ISO-8601 strings (UTC); jsonb decoded.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .records import Form, Segment, SegmentKind

SCHEMA = Path(__file__).with_name("schema.sql")
WORKSPACE = "*"          # the workspace version: bumped on every change
BLOB_MIN_BYTES = 4096    # text at least this large is also written to blobs


def now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(row: dict | None) -> dict | None:
    """A row as plain Python: timestamps as ISO strings, bytea as bytes."""
    if row is None:
        return None
    out = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            value = value.isoformat()
        elif isinstance(value, memoryview):
            value = bytes(value)
        out[key] = value
    return out


class PostgresBlobStore:
    """Content-addressed, write-once. blob_key = sha256(data) hex."""

    def __init__(self, db: "PostgresAgentDB"):
        self.db = db

    def put(self, data: bytes) -> str:
        import hashlib
        key = hashlib.sha256(data).hexdigest()
        self.db.execute("INSERT INTO blobs (blob_key, size, data, created_at) VALUES (%s,%s,%s,%s) "
                        "ON CONFLICT (blob_key) DO NOTHING", (key, len(data), data, now()))
        return key

    def get(self, blob_key: str) -> bytes:
        rows = self.db.rows("SELECT data FROM blobs WHERE blob_key=%s", (blob_key,))
        if not rows:
            raise KeyError(blob_key)
        return rows[0]["data"]


class PostgresAgentDB:
    def __init__(self, dsn: str, blob_min_bytes: int = BLOB_MIN_BYTES):
        self.conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute(SCHEMA.read_text())
        self.blobs = PostgresBlobStore(self)
        self.blob_min_bytes = blob_min_bytes

    def close(self) -> None:
        self.conn.close()

    # -- plain SQL (for tests and inspection; not used by the Owner) ---------------------

    def execute(self, sql: str, args=()) -> None:
        with self.lock:
            self.conn.execute(sql, args)

    def rows(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            return [_clean(r) for r in self.conn.execute(sql, args).fetchall()]

    def _one(self, sql: str, args=()) -> dict | None:
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    # -- runs ---------------------------------------------------------------------------

    def start_run(self, run_id: str, task: str) -> None:
        """A new run. A run id is used once: starting it again is an error, so two trials never share
        a window."""
        self.execute("INSERT INTO runs (run_id, task) VALUES (%s,%s)", (run_id, task))

    def get_run(self, run_id: str) -> dict | None:
        return self._one("SELECT * FROM runs WHERE run_id=%s", (run_id,))

    # -- segments and the window ----------------------------------------------------------

    def _blob(self, text: str) -> str | None:
        data = text.encode()
        return self.blobs.put(data) if len(data) >= self.blob_min_bytes else None

    def add_segment(self, s: Segment) -> None:
        """Write-once: the same identity is never rewritten."""
        if self._one("SELECT 1 FROM segments WHERE segment_id=%s", (s.id,)):
            return
        kind = s.kind.value if hasattr(s.kind, "value") else s.kind
        self.execute(
            "INSERT INTO segments (segment_id, run_id, native_id, kind, text, tokens, content_hash, source, "
            "version, pinned, needs_exact_bytes, pair_id, role, created_at, blob_key) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (segment_id) DO NOTHING",
            (s.id, s.run_id, s.native_id, kind, s.text, s.tokens, s.content_hash, s.source, s.version,
             bool(s.pinned), bool(s.needs_exact_bytes), s.pair_id, s.role, now(), self._blob(s.text)))

    def segment(self, segment_id: str) -> Segment | None:
        row = self._one("SELECT * FROM segments WHERE segment_id=%s", (segment_id,))
        return row_to_segment(row) if row else None

    def set_window(self, run_id: str, segments: list[Segment]) -> None:
        """Atomic: the run's window becomes exactly these, each at its position and part, in its form,
        with its token count (window_tokens, else the segment's own)."""
        t = now()
        with self.lock, self.conn.transaction():
            self.conn.execute("UPDATE window_entries SET in_window=false, updated_at=%s "
                              "WHERE run_id=%s AND in_window", (t, run_id))
            for s in segments:
                self.conn.execute(
                    "INSERT INTO window_entries (run_id, segment_id, position, part, in_window, form, tokens, "
                    "updated_at) VALUES (%s,%s,%s,%s,true,%s,%s,%s) ON CONFLICT (run_id, segment_id) DO UPDATE "
                    "SET position=excluded.position, part=excluded.part, in_window=true, form=excluded.form, "
                    "tokens=excluded.tokens, updated_at=excluded.updated_at",
                    (run_id, s.id, s.position, s.part, Form(s.form).value,
                     s.tokens if s.window_tokens is None else s.window_tokens, t))

    def window(self, run_id: str) -> list[Segment]:
        """The window in order: by position, then part."""
        rows = self.rows(
            "SELECT s.*, w.position, w.part, w.in_window, w.form, w.tokens AS window_tokens FROM window_entries w "
            "JOIN segments s ON s.segment_id = w.segment_id "
            "WHERE w.run_id=%s AND w.in_window ORDER BY w.position, w.part", (run_id,))
        return [row_to_segment(r) for r in rows]

    def window_results(self, run_id: str) -> list[dict]:
        """The tool results shown in the window, oldest first: each with how it was called, whether it is
        still valid, its full text, and the segment shown for it (its position, part, form and tokens)."""
        return self.rows(
            "SELECT t.tool_result_id, t.tool_name, t.args_key, t.args, t.read_set, t.side_effect, t.valid, "
            "r.text AS full_text, r.tokens AS full_tokens, s.segment_id, s.pair_id, s.text, w.position, w.part, "
            "w.form, w.tokens FROM window_entries w "
            "JOIN segments s ON s.segment_id = w.segment_id AND s.role = 'tool' "
            "JOIN segments r ON r.run_id = s.run_id AND r.pair_id = s.pair_id "
            "JOIN tool_results t ON t.run_id = w.run_id AND t.result_segment_id = r.segment_id "
            "WHERE w.run_id=%s AND w.in_window ORDER BY t.seq", (run_id,))

    # -- tool results and versions --------------------------------------------------------

    def current_versions(self, run_id: str) -> dict[str, str]:
        rows = self.rows("SELECT source, version FROM source_versions WHERE run_id=%s", (run_id,))
        return {r["source"]: r["version"] for r in rows}

    def bump(self, run_id: str, sources: list[str]) -> None:
        """Advance each source's version (and the workspace version), then invalidate. Atomic.

        The increment happens in SQL, on the locked row, never as read-then-write in Python: with several
        writers a Python-side "+1" lets two bumps both write the same number, and a result read between
        them stays valid after the file changed again."""
        with self.lock, self.conn.transaction():
            t = now()
            for source in sorted({*sources, WORKSPACE}):  # one lock order: no deadlocks
                self.conn.execute(
                    "INSERT INTO source_versions VALUES (%s,%s,'1',%s) ON CONFLICT (run_id, source) "
                    "DO UPDATE SET version=(source_versions.version::bigint + 1)::text, "
                    "updated_at=excluded.updated_at", (run_id, source, t))
            self.invalidate(run_id)

    def invalidate(self, run_id: str) -> None:
        """valid = false where a read set no longer matches the current versions."""
        self.execute(
            "UPDATE tool_results t SET valid=false WHERE t.run_id=%s AND t.valid AND EXISTS ("
            "SELECT 1 FROM jsonb_each_text(t.read_set) r WHERE r.value <> COALESCE("
            "(SELECT v.version FROM source_versions v WHERE v.run_id=t.run_id AND v.source=r.key), '0'))",
            (run_id,))

    def add_tool_result(self, row_id: str, run_id: str, tool_name: str, args_key: str, args: dict,
                        segment_id: str, read_set: dict, side_effect: bool) -> None:
        self.execute(
            "INSERT INTO tool_results (run_id, tool_result_id, tool_name, args_key, args, result_segment_id, "
            "read_set, side_effect, valid, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,%s) "
            "ON CONFLICT (run_id, tool_result_id) DO NOTHING",
            (run_id, row_id, tool_name, args_key, Jsonb(args), segment_id, Jsonb(read_set), bool(side_effect),
             now()))

    def find_tool_result(self, run_id: str, tool_name: str, args_key: str) -> dict | None:
        """The latest valid, side-effect-free result of this call, with its text and tokens."""
        return self._one(
            "SELECT t.tool_result_id, t.read_set, s.text, s.tokens FROM tool_results t JOIN segments s "
            "ON s.segment_id = t.result_segment_id WHERE t.run_id=%s AND t.tool_name=%s AND "
            "t.args_key=%s AND t.valid AND NOT t.side_effect ORDER BY t.seq DESC LIMIT 1",
            (run_id, tool_name, args_key))


def row_to_segment(r: dict) -> Segment:
    return Segment(
        id=r["segment_id"], run_id=r["run_id"], native_id=r["native_id"], kind=SegmentKind(r["kind"]),
        text=r["text"], tokens=r["tokens"], content_hash=r["content_hash"], source=r["source"],
        version=r["version"], pinned=bool(r["pinned"]), needs_exact_bytes=bool(r["needs_exact_bytes"]),
        pair_id=r["pair_id"], role=r["role"], position=r.get("position", 0), part=r.get("part", 0),
        in_window=bool(r.get("in_window", True)), form=Form(r.get("form", "FULL")),
        window_tokens=r.get("window_tokens"))
