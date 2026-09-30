"""The Agent DB: every write and read EconoContext makes, in plain SQL.

Why it exists: it is the centre of the system. The planner retrieves from it
instead of recomputing, the optimizer's decisions and the provider's reported
usage are joined in it, and future predictors will learn from it.
What it must never do: decide anything, or hold provider- or host-specific logic.
Segments are written once and never rewritten; only window state changes.
"""

import json
import os
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..types import AgentNode, Decision, ProviderUsage, Representation, Segment, SegmentKind, Zone
from .blobs import DBBlobStore

SCHEMA = Path(__file__).with_name("schema.sql")
WORKSPACE = "*"  # the workspace epoch: bumped on every change
# Content at least this large is also written to the blob store, so identical large
# content is stored once. A judgement value (about one screen of text); tune freely.
BLOB_MIN_BYTES = 4096


def read_paths(tool_call_text: str) -> set[str]:
    """Paths of sys_os_read calls in a tool-call segment (text + a JSON list of calls)."""
    try:
        calls = json.loads(tool_call_text.rsplit("\n", 1)[-1])
    except (ValueError, IndexError):
        return set()
    paths = set()
    for call in calls if isinstance(calls, list) else []:
        if call.get("name") == "sys_os_read":
            try:
                args = json.loads(call.get("args") or "{}")
            except ValueError:
                continue
            if isinstance(args.get("path"), str):
                paths.add(os.path.normpath(args["path"]))
    return paths


@dataclass
class Snapshot:
    """One run's state, read together (store.snapshot). Planner reads go through the
    same data; a snapshot is the unit a future multi-worker scheduler can hand out."""
    run: dict
    versions: dict[str, str]
    agents: list[dict]
    windows: dict[str, list[str]]           # agent_id -> segment ids in its window, in order
    valid_tool_results: list[dict]
    valid_stored_results: list[dict]
    taken_at: str = field(default="")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentDB:
    def __init__(self, path: str | Path, blob_min_bytes: int = BLOB_MIN_BYTES):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False plus one lock: host callbacks may arrive on worker threads.
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        schema = SCHEMA.read_text()
        try:
            self.conn.executescript(schema)
            self.has_fts = True
        except sqlite3.OperationalError as exc:
            if "fts5" not in str(exc).lower():
                raise
            # This SQLite build has no FTS5: create everything except the index and
            # its triggers; retrieval falls back to LIKE (slower, same results shape).
            head, _, rest = schema.partition("-- FTS BEGIN")
            self.conn.executescript(head + rest.partition("-- FTS END")[2])
            self.has_fts = False
        # Databases created before these columns existed get them added.
        self._add_column("runs", "jev", "INTEGER NOT NULL DEFAULT 0")
        self._add_column("decisions", "prediction", "TEXT")
        self._add_column("decisions", "subject_id", "TEXT")
        self._add_column("decisions", "payloads", "TEXT")
        self._add_column("runs", "overrides", "TEXT")
        self._add_column("runs", "workdir", "TEXT")
        self._add_column("segments", "blob_key", "TEXT")
        self._add_column("stored_results", "blob_key", "TEXT")
        self.conn.commit()
        self.blobs = DBBlobStore(self)
        self.blob_min_bytes = blob_min_bytes

    def _add_column(self, table: str, column: str, declaration: str) -> None:
        if column not in {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def execute(self, sql: str, args=()) -> None:
        with self.lock:
            self.conn.execute(sql, args)
            self.conn.commit()

    def rows(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    # -- runs and agents ----------------------------------------------------------

    def start_run(self, run_id, host, instance_id, arm, mode, model, temperature, fingerprint,
                  jev: bool = False, overrides: dict | None = None, workdir: str | None = None):
        self.execute("INSERT OR IGNORE INTO runs (run_id, host, instance_id, arm, mode, model, "
                     "temperature, config_fingerprint, started_at, jev, overrides, workdir) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (run_id, host, instance_id, arm, mode, model, temperature, fingerprint,
                      now(), int(jev), json.dumps(overrides) if overrides else None, workdir))

    def cache_share(self, run_id: str) -> float | None:
        """Share of this run's input tokens served from cache so far (None before any call)."""
        r = self.rows("SELECT COALESCE(SUM(uncached_input),0) u, COALESCE(SUM(cache_read),0) c "
                      "FROM outcomes WHERE run_id=?", (run_id,))[0]
        return r["c"] / (r["u"] + r["c"]) if r["u"] + r["c"] else None

    def call_count_at(self, agent_id: str, segment_id: str) -> int:
        """The agent's call count when a segment first appeared (its age reference)."""
        return self.rows("SELECT COUNT(*) n FROM outcomes o, segments s WHERE s.segment_id=? "
                         "AND o.agent_id=? AND o.created_at < s.created_at",
                         (segment_id, agent_id))[0]["n"]

    def calls_so_far(self, agent_id: str) -> int:
        """Model calls this agent has made (recorded at the gateway), for H_hat."""
        return self.rows("SELECT COUNT(*) n FROM outcomes WHERE agent_id=?", (agent_id,))[0]["n"]

    def end_run(self, run_id: str, status: str) -> None:
        self.execute("UPDATE runs SET ended_at=?, status=? WHERE run_id=?", (now(), status, run_id))

    def set_resolved(self, run_id: str, resolved: bool) -> None:
        self.execute("UPDATE runs SET resolved=? WHERE run_id=?", (int(resolved), run_id))

    def upsert_agent(self, run_id: str, node: AgentNode) -> None:
        self.execute(
            "INSERT INTO agents VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET "
            "status=excluded.status, updated_at=excluded.updated_at",
            (node.agent_id, run_id, node.parent_id, node.subagent_type, node.status.value,
             node.window_max_tokens, now(), now()))

    # -- segments and windows ----------------------------------------------------

    def _blob(self, text: str) -> str | None:
        data = text.encode()
        return self.blobs.put(data) if len(data) >= self.blob_min_bytes else None

    def add_segment(self, s: Segment) -> None:
        """Write-once: a segment with the same identity is never rewritten. Large text is
        also put in the blob store (blob_key); the text column stays as it is."""
        if self.rows("SELECT 1 FROM segments WHERE segment_id=?", (s.id,)):
            return
        self.execute(
            "INSERT OR IGNORE INTO segments (segment_id, run_id, agent_id, native_id, kind, text, "
            "tokens, content_hash, source, version, pinned, needs_exact_bytes, pair_id, role, "
            "created_at, blob_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (s.id, s.run_id, s.agent_id, s.native_id, s.kind.value, s.text, s.tokens,
             s.content_hash, s.source, s.version, int(s.pinned), int(s.needs_exact_bytes),
             s.pair_id, s.role, now(), self._blob(s.text)))

    def set_window(self, agent_id: str, segments: list[Segment]) -> None:
        """Record the agent's current window: these segments, in this order, are in it."""
        with self.lock:
            self.conn.execute("UPDATE window_entries SET in_window=0, updated_at=? "
                              "WHERE agent_id=?", (now(), agent_id))
            for s in segments:
                self.conn.execute(
                    "INSERT INTO window_entries VALUES(?,?,?,1,?,?,?) ON CONFLICT(agent_id, "
                    "segment_id) DO UPDATE SET position=excluded.position, in_window=1, "
                    "representation=excluded.representation, zone=excluded.zone, "
                    "updated_at=excluded.updated_at",
                    (agent_id, s.id, s.position, s.representation.value, s.zone.value, now()))
            self.conn.commit()

    def window(self, agent_id: str) -> list[Segment]:
        rows = self.rows("SELECT s.*, w.position, w.in_window, w.representation, w.zone "
                         "FROM segments s JOIN window_entries w ON w.segment_id = s.segment_id "
                         "AND w.agent_id = s.agent_id WHERE w.agent_id=? AND w.in_window=1 "
                         "ORDER BY w.position", (agent_id,))
        return [row_to_segment(r) for r in rows]

    def segment(self, segment_id: str) -> Segment | None:
        rows = self.rows("SELECT * FROM segments WHERE segment_id=?", (segment_id,))
        return row_to_segment(rows[0]) if rows else None

    # -- versions and the write barrier ----------------------------------------

    def current_versions(self, run_id: str) -> dict[str, str]:
        rows = self.rows("SELECT source, version FROM source_versions WHERE run_id=?", (run_id,))
        return {r["source"]: r["version"] for r in rows}

    def bump(self, run_id: str, sources: list[str]) -> None:
        """Advance each source's version (and the workspace epoch), then invalidate."""
        current = self.current_versions(run_id)
        for source in {*sources, WORKSPACE}:
            version = str(int(current.get(source, "0")) + 1)
            self.execute("INSERT INTO source_versions VALUES(?,?,?,?) ON CONFLICT(run_id, "
                         "source) DO UPDATE SET version=excluded.version, "
                         "updated_at=excluded.updated_at", (run_id, source, version, now()))
        self.invalidate(run_id)

    def invalidate(self, run_id: str) -> None:
        """Mark every stored tool result or delegated result whose read-set is stale."""
        current = self.current_versions(run_id)
        for table, key in (("tool_results", "tool_result_id"), ("stored_results", "result_id")):
            for r in self.rows(f"SELECT {key}, read_set FROM {table} WHERE run_id=? AND valid=1",
                               (run_id,)):
                read = json.loads(r["read_set"])
                if any(current.get(src, "0") != ver for src, ver in read.items()):
                    self.execute(f"UPDATE {table} SET valid=0 WHERE {key}=?", (r[key],))

    def dependents(self, run_id: str, source: str) -> dict[str, list[str]]:
        """Everything that depends on `source` (a file path, or '*' for the workspace):
        tool results and stored results whose read set names it, and workers that read it.
        These are what a change to `source` makes stale."""
        source = source if source == WORKSPACE else os.path.normpath(source)

        def reading(table: str, key: str) -> list[str]:
            return [r[key] for r in self.rows(f"SELECT {key}, read_set FROM {table} WHERE run_id=? "
                                             f"ORDER BY created_at", (run_id,))
                    if source in json.loads(r["read_set"])]

        workers = sorted({r["agent_id"] for r in self.rows(
            "SELECT agent_id, text FROM segments WHERE run_id=? AND kind='tool_call' AND "
            "agent_id NOT LIKE '%:root'", (run_id,)) if source in read_paths(r["text"])})
        return dict(tool_results=reading("tool_results", "tool_result_id"),
                    stored_results=reading("stored_results", "result_id"), workers=workers)

    def snapshot(self, run_id: str) -> Snapshot:
        """The run's state read together, under one lock.
        Wraps today's reads; the planner does not use it yet."""
        with self.lock:
            run = self.rows("SELECT * FROM runs WHERE run_id=?", (run_id,))
            agents = [dict(r) for r in self.rows("SELECT * FROM agents WHERE run_id=? ORDER BY "
                                                 "created_at", (run_id,))]
            windows = {}
            for a in agents:
                windows[a["agent_id"]] = [r["segment_id"] for r in self.rows(
                    "SELECT segment_id FROM window_entries WHERE agent_id=? AND in_window=1 "
                    "ORDER BY position", (a["agent_id"],))]
            return Snapshot(
                run=dict(run[0]) if run else {}, versions=self.current_versions(run_id),
                agents=agents, windows=windows,
                valid_tool_results=[dict(r) for r in self.rows(
                    "SELECT tool_result_id, tool_name, args_key, read_set FROM tool_results "
                    "WHERE run_id=? AND valid=1", (run_id,))],
                valid_stored_results=[dict(r) for r in self.rows(
                    "SELECT result_id, task_key, read_set FROM stored_results WHERE run_id=? "
                    "AND valid=1", (run_id,))],
                taken_at=now())

    # -- reusable results ---------------------------------------------------------

    def add_tool_result(self, row_id, run_id, agent_id, tool_name, args_key, segment_id,
                        read_set, side_effect) -> None:
        self.execute("INSERT OR IGNORE INTO tool_results VALUES(?,?,?,?,?,?,?,?,1,?)",
                     (row_id, run_id, agent_id, tool_name, args_key, segment_id,
                      json.dumps(read_set, sort_keys=True), int(side_effect), now()))

    def find_tool_result(self, run_id, tool_name, args_key):
        rows = self.rows("SELECT t.tool_result_id, t.read_set, s.text FROM tool_results t JOIN "
                         "segments s ON s.segment_id = t.result_segment_id WHERE t.run_id=? AND "
                         "t.tool_name=? AND t.args_key=? AND t.valid=1 AND t.side_effect=0 "
                         "ORDER BY t.created_at DESC LIMIT 1", (run_id, tool_name, args_key))
        return rows[0] if rows else None

    def add_stored_result(self, result_id, run_id, task_key, agent_id, text, read_set,
                          side_effect) -> None:
        self.execute("INSERT OR IGNORE INTO stored_results (result_id, run_id, task_key, agent_id, "
                     "result_text, read_set, side_effect, valid, created_at, blob_key) "
                     "VALUES(?,?,?,?,?,?,?,1,?,?)",
                     (result_id, run_id, task_key, agent_id, text,
                      json.dumps(read_set, sort_keys=True), int(side_effect), now(),
                      self._blob(text)))

    def find_stored_result(self, run_id, task_key):
        rows = self.rows("SELECT result_id, read_set, result_text FROM stored_results WHERE "
                         "run_id=? AND task_key=? AND valid=1 AND side_effect=0 ORDER BY "
                         "created_at DESC LIMIT 1", (run_id, task_key))
        return rows[0] if rows else None

    # -- decisions and outcomes ------------------------------------------------------

    def add_decision(self, run_id, agent_id, d: Decision, mode, decision_ms, error=None,
                     cache_predicted=None, manifest_hash=None) -> None:
        self.execute(
            "INSERT INTO decisions (decision_id, run_id, agent_id, intercept, mode, candidates, "
            "feasible, chosen, applied, predicted_cost, why_not, cache_predicted, manifest_hash, "
            "decision_ms, error, created_at, prediction, subject_id, payloads) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d.id, run_id, agent_id, d.intercept.value, mode,
             json.dumps({k: asdict(v) for k, v in d.candidate_costs.items()}),
             json.dumps(d.feasible), d.chosen.name, int(d.applied), json.dumps(asdict(d.cost)),
             json.dumps([asdict(r) for r in d.rejected]), cache_predicted, manifest_hash,
             decision_ms, error, now(), None if d.prediction is None else json.dumps(d.prediction),
             d.subject_id, json.dumps(d.payloads, default=str)))

    def add_outcome(self, outcome_id, run_id, agent_id, decision_id, phase, u: ProviderUsage,
                    cost_nu, cost_usd, complete, period) -> None:
        self.execute(
            "INSERT OR IGNORE INTO outcomes "
            "(outcome_id, run_id, agent_id, decision_id, phase, uncached_input, cache_read, "
            "cache_write, output, reasoning, latency_ms, cost_nu, cost_usd, cost_complete, "
            "price_period, raw, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (outcome_id, run_id, agent_id, decision_id, phase, u.uncached_input,
             u.cache_read, u.cache_write, u.output, u.reasoning, u.latency_ms,
             cost_nu, cost_usd, int(complete), period, json.dumps(u.raw, default=str), now()))

    # -- runtime timing -----------------------------------------------------------

    def add_runtime_span(self, span_id: str, run_id: str, agent_id: str, kind: str,
                         name: str, native_id: str | None, decision_id: str | None,
                         metadata: dict | None = None) -> None:
        self.execute(
            "INSERT OR IGNORE INTO runtime_spans "
            "(span_id, run_id, agent_id, kind, name, native_id, decision_id, started_at, "
            "status, metadata) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (span_id, run_id, agent_id, kind, name, native_id, decision_id, now(), "open",
             json.dumps(metadata or {}, sort_keys=True)))

    def finish_runtime_span(self, span_id: str, duration_ms: float, status: str,
                            metadata: dict | None = None) -> None:
        """Close a span; `metadata` (what was learned only at the end) is merged in."""
        with self.lock:
            if metadata:
                row = self.conn.execute("SELECT metadata FROM runtime_spans WHERE span_id=?",
                                        (span_id,)).fetchone()
                merged = {**json.loads(row["metadata"] if row else "{}"), **metadata}
                self.conn.execute("UPDATE runtime_spans SET metadata=? WHERE span_id=?",
                                  (json.dumps(merged, sort_keys=True), span_id))
            self.conn.execute("UPDATE runtime_spans SET ended_at=?, duration_ms=?, status=? "
                              "WHERE span_id=?", (now(), duration_ms, status, span_id))
            self.conn.commit()


def row_to_segment(r: sqlite3.Row) -> Segment:
    keys = r.keys()
    return Segment(
        id=r["segment_id"], run_id=r["run_id"], agent_id=r["agent_id"], native_id=r["native_id"],
        kind=SegmentKind(r["kind"]), text=r["text"], tokens=r["tokens"],
        content_hash=r["content_hash"], source=r["source"], version=r["version"],
        pinned=bool(r["pinned"]), needs_exact_bytes=bool(r["needs_exact_bytes"]),
        pair_id=r["pair_id"], role=r["role"],
        zone=Zone(r["zone"]) if "zone" in keys else Zone.WARM,
        representation=Representation(r["representation"]) if "representation" in keys
        else Representation.FULL,
        position=r["position"] if "position" in keys else 0,
        in_window=bool(r["in_window"]) if "in_window" in keys else True)
