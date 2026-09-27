"""The Agent DB: every write and read EconoContext makes, in plain SQL.

Why it exists: it is the centre of the system. The planner retrieves from it
instead of recomputing, the optimizer's decisions and the provider's reported
usage are joined in it, and future predictors will learn from it.
What it must never do: decide anything, or hold provider- or host-specific logic.
Segments are written once and never rewritten; only window state changes.
"""

import json
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ..types import AgentNode, Decision, ProviderUsage, Representation, Segment, SegmentKind, Zone

SCHEMA = Path(__file__).with_name("schema.sql")
WORKSPACE = "*"  # the workspace epoch: bumped on every change


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentDB:
    def __init__(self, path: str | Path):
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
        self.conn.commit()

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
                  jev: bool = False):
        self.execute("INSERT OR IGNORE INTO runs (run_id, host, instance_id, arm, mode, model, "
                     "temperature, config_fingerprint, started_at, jev) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (run_id, host, instance_id, arm, mode, model, temperature, fingerprint,
                      now(), int(jev)))

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

    def add_segment(self, s: Segment) -> None:
        """Write-once: a segment with the same identity is never rewritten."""
        self.execute(
            "INSERT OR IGNORE INTO segments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (s.id, s.run_id, s.agent_id, s.native_id, s.kind.value, s.text, s.tokens,
             s.content_hash, s.source, s.version, int(s.pinned), int(s.needs_exact_bytes),
             s.pair_id, s.role, now()))

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
        self.execute("INSERT OR IGNORE INTO stored_results VALUES(?,?,?,?,?,?,?,1,?)",
                     (result_id, run_id, task_key, agent_id, text,
                      json.dumps(read_set, sort_keys=True), int(side_effect), now()))

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
            "decision_ms, error, created_at, prediction) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d.id, run_id, agent_id, d.intercept.value, mode,
             json.dumps({k: asdict(v) for k, v in d.candidate_costs.items()}),
             json.dumps(d.feasible), d.chosen.name, int(d.applied), json.dumps(asdict(d.cost)),
             json.dumps([asdict(r) for r in d.rejected]), cache_predicted, manifest_hash,
             decision_ms, error, now(), None if d.prediction is None else json.dumps(d.prediction)))

    def add_outcome(self, outcome_id, run_id, agent_id, decision_id, phase, u: ProviderUsage,
                    cost_nu, cost_usd, complete, period) -> None:
        self.execute("INSERT OR IGNORE INTO outcomes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (outcome_id, run_id, agent_id, decision_id, phase, u.uncached_input,
                      u.cache_read, u.cache_write, u.output, u.reasoning, u.latency_ms,
                      cost_nu, cost_usd, int(complete), period, json.dumps(u.raw, default=str),
                      now()))


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
