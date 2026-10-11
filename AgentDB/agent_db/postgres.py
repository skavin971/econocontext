"""PostgresAgentDB: Part B of AGENT_DB_CONTRACT.md on Postgres.

Mirrors econocontext/store/db.py (the SQLite reference) method for method, and adds
the operations the contract names that the reference only has as SQL in its callers.

Conventions
- Every operation takes the one lock; host callbacks may arrive on several threads.
- Writes that must be atomic (set_window, replace_labels, bump) run in one transaction;
  snapshot reads in one REPEATABLE READ transaction.
- Rows come back as dicts. timestamptz values are returned as ISO-8601 strings (UTC),
  so results compare and sort the same way as the reference's text timestamps.
  jsonb columns come back decoded (dict/list), not as JSON text.
- Ids and timestamps are chosen here, in Python, exactly as the reference does.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from econocontext.types import (AgentNode, Decision, ProviderUsage, Representation, Segment,
                                SegmentKind, Zone)

SCHEMA = Path(__file__).with_name("schema.sql")
WORKSPACE = "*"          # the workspace epoch: bumped on every change
BLOB_MIN_BYTES = 4096    # text at least this large is also written to blobs


def now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


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


def read_paths(tool_call_text: str) -> set[str]:
    """Paths of sys_os_read calls in a tool-call segment (text + a JSON list of calls).
    Same rule as the reference, so `dependents` agrees across backends."""
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
    """One run's state, read together in one transaction."""
    run: dict
    versions: dict[str, str]
    agents: list[dict]
    windows: dict[str, list[str]]
    valid_tool_results: list[dict]
    valid_stored_results: list[dict]
    taken_at: str = field(default="")


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
        self.has_fts = True

    def close(self) -> None:
        self.conn.close()

    # -- plain SQL (kept for parity with the reference; not part of Part B) -----------

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

    def start_run(self, run_id, host, instance_id, arm, mode, model, temperature, fingerprint,
                  jev: bool = False, overrides: dict | None = None, workdir: str | None = None):
        self.execute(
            "INSERT INTO runs (run_id, host, instance_id, arm, mode, model, temperature, "
            "config_fingerprint, started_at, jev, overrides, workdir) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (run_id) DO NOTHING",
            (run_id, host, instance_id, arm, mode, model, temperature, fingerprint, now(),
             bool(jev), Jsonb(overrides) if overrides else None, workdir))

    def end_run(self, run_id: str, status: str) -> None:
        self.execute("UPDATE runs SET ended_at=%s, status=%s WHERE run_id=%s", (now(), status, run_id))

    def set_resolved(self, run_id: str, resolved: bool) -> None:
        self.execute("UPDATE runs SET resolved=%s WHERE run_id=%s", (bool(resolved), run_id))

    def get_run(self, run_id: str) -> dict | None:
        return self._one("SELECT * FROM runs WHERE run_id=%s", (run_id,))

    def list_runs(self, label: str) -> list[dict]:
        return self.rows("SELECT * FROM runs WHERE run_id LIKE %s ORDER BY run_id", (f"{label}:%",))

    def labelled_runs(self, exclude_instance: str | None = None) -> list[str]:
        rows = self.rows(
            "SELECT DISTINCT r.run_id FROM runs r JOIN labels l ON l.run_id = r.run_id "
            "WHERE %s::text IS NULL OR r.instance_id IS DISTINCT FROM %s ORDER BY r.run_id",
            (exclude_instance, exclude_instance))
        return [r["run_id"] for r in rows]

    # -- agents, segments, windows ------------------------------------------------------

    def upsert_agent(self, run_id: str, node: AgentNode) -> None:
        status = node.status.value if hasattr(node.status, "value") else node.status
        t = now()
        self.execute(
            "INSERT INTO agents VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (agent_id) DO UPDATE "
            "SET status=excluded.status, updated_at=excluded.updated_at",
            (node.agent_id, run_id, node.parent_id, node.subagent_type, status,
             node.window_max_tokens, t, t))

    def _blob(self, text: str) -> str | None:
        data = text.encode()
        return self.blobs.put(data) if len(data) >= self.blob_min_bytes else None

    def add_segment(self, s: Segment) -> None:
        """Write-once: the same identity is never rewritten."""
        if self._one("SELECT 1 FROM segments WHERE segment_id=%s", (s.id,)):
            return
        kind = s.kind.value if hasattr(s.kind, "value") else s.kind
        self.execute(
            "INSERT INTO segments (segment_id, run_id, agent_id, native_id, kind, text, tokens, "
            "content_hash, source, version, pinned, needs_exact_bytes, pair_id, role, created_at, "
            "blob_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (segment_id) DO NOTHING",
            (s.id, s.run_id, s.agent_id, s.native_id, kind, s.text, s.tokens, s.content_hash,
             s.source, s.version, bool(s.pinned), bool(s.needs_exact_bytes), s.pair_id, s.role,
             now(), self._blob(s.text)))

    def segment(self, segment_id: str) -> Segment | None:
        row = self._one("SELECT * FROM segments WHERE segment_id=%s", (segment_id,))
        return row_to_segment(row) if row else None

    def set_window(self, agent_id: str, segments: list[Segment]) -> None:
        """Atomic: the agent's window becomes exactly these, in order."""
        t = now()
        with self.lock, self.conn.transaction():
            self.conn.execute("UPDATE window_entries SET in_window=false, updated_at=%s "
                              "WHERE agent_id=%s", (t, agent_id))
            for s in segments:
                self.conn.execute(
                    "INSERT INTO window_entries VALUES (%s,%s,%s,true,%s,%s,%s) "
                    "ON CONFLICT (agent_id, segment_id) DO UPDATE SET position=excluded.position, "
                    "in_window=true, representation=excluded.representation, zone=excluded.zone, "
                    "updated_at=excluded.updated_at",
                    (agent_id, s.id, s.position, s.representation.value, s.zone.value, t))

    def window(self, agent_id: str) -> list[Segment]:
        rows = self.rows(
            "SELECT s.*, w.position, w.in_window, w.representation, w.zone FROM segments s "
            "JOIN window_entries w ON w.segment_id = s.segment_id AND w.agent_id = s.agent_id "
            "WHERE w.agent_id=%s AND w.in_window ORDER BY w.position", (agent_id,))
        return [row_to_segment(r) for r in rows]

    def first_segment(self, agent_id: str, kind) -> Segment | None:
        kind = kind.value if hasattr(kind, "value") else kind
        row = self._one("SELECT * FROM segments WHERE agent_id=%s AND kind=%s "
                        "ORDER BY created_at LIMIT 1", (agent_id, kind))
        return row_to_segment(row) if row else None

    def segments_of(self, agent_id: str, kind) -> list[Segment]:
        kind = kind.value if hasattr(kind, "value") else kind
        rows = self.rows("SELECT * FROM segments WHERE agent_id=%s AND kind=%s ORDER BY created_at",
                         (agent_id, kind))
        return [row_to_segment(r) for r in rows]

    def assistant_segments(self, run_id: str) -> list[dict]:
        """Every assistant-role segment of the run, oldest first (what labels quote-match)."""
        return self.rows("SELECT segment_id, agent_id, text, created_at FROM segments "
                         "WHERE run_id=%s AND role='assistant' ORDER BY created_at", (run_id,))

    def search_segments(self, run_id: str, terms: list[str], limit: int = 5) -> list[Segment]:
        """Best matches first: segments of this run containing the most terms, newest first
        among equals; one per distinct content."""
        terms = [t for t in terms if t]
        if not terms:
            return []
        score = " + ".join("(text ILIKE %s)::int" for _ in terms)
        where = " OR ".join("text ILIKE %s" for _ in terms)
        patterns = [f"%{t}%" for t in terms]
        rows = self.rows(
            f"SELECT *, ({score}) AS score FROM segments WHERE run_id=%s AND ({where}) "
            f"ORDER BY score DESC, created_at DESC LIMIT %s",
            (*patterns, run_id, *patterns, limit * 4))
        results, seen = [], set()
        for r in rows:
            if r["content_hash"] in seen:
                continue
            seen.add(r["content_hash"])
            results.append(row_to_segment(r))
            if len(results) == limit:
                break
        return results

    # -- versions, reuse, dependents ----------------------------------------------------

    def current_versions(self, run_id: str) -> dict[str, str]:
        rows = self.rows("SELECT source, version FROM source_versions WHERE run_id=%s", (run_id,))
        return {r["source"]: r["version"] for r in rows}

    def bump(self, run_id: str, sources: list[str]) -> None:
        """Advance each source's version (and the workspace epoch), then invalidate. Atomic.

        The increment happens in SQL, on the locked row, never as read-then-write in
        Python: with several writers (the gateway process, the host process, parallel
        workers) a Python-side "+1" lets two bumps both write the same number, and a
        result read between them stays valid after the file changed again."""
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
        for table, key in (("tool_results", "tool_result_id"), ("stored_results", "result_id")):
            self.execute(
                f"UPDATE {table} t SET valid=false WHERE t.run_id=%s AND t.valid AND EXISTS ("
                f"SELECT 1 FROM jsonb_each_text(t.read_set) r WHERE r.value <> COALESCE("
                f"(SELECT v.version FROM source_versions v WHERE v.run_id=t.run_id "
                f"AND v.source=r.key), '0'))", (run_id,))

    def dependents(self, run_id: str, source: str) -> dict[str, list[str]]:
        """Tool results and stored results whose read set names `source`, and the workers
        that read it. Paths are normalized."""
        source = source if source == WORKSPACE else os.path.normpath(source)
        tool = [r["tool_result_id"] for r in self.rows(
            "SELECT tool_result_id FROM tool_results WHERE run_id=%s AND read_set ? %s "
            "ORDER BY created_at", (run_id, source))]
        stored = [r["result_id"] for r in self.rows(
            "SELECT result_id FROM stored_results WHERE run_id=%s AND read_set ? %s "
            "ORDER BY created_at", (run_id, source))]
        workers = sorted({r["agent_id"] for r in self.rows(
            "SELECT agent_id, text FROM segments WHERE run_id=%s AND kind='tool_call' "
            "AND agent_id NOT LIKE '%%:root'", (run_id,)) if source in read_paths(r["text"])})
        return dict(tool_results=tool, stored_results=stored, workers=workers)

    def add_tool_result(self, row_id, run_id, agent_id, tool_name, args_key, segment_id,
                        read_set, side_effect) -> None:
        self.execute(
            "INSERT INTO tool_results VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,%s) "
            "ON CONFLICT (tool_result_id) DO NOTHING",
            (row_id, run_id, agent_id, tool_name, args_key, segment_id, Jsonb(read_set),
             bool(side_effect), now()))

    def find_tool_result(self, run_id, tool_name, args_key) -> dict | None:
        """Latest valid, side-effect-free match, with its text."""
        return self._one(
            "SELECT t.tool_result_id, t.read_set, s.text FROM tool_results t JOIN segments s "
            "ON s.segment_id = t.result_segment_id WHERE t.run_id=%s AND t.tool_name=%s AND "
            "t.args_key=%s AND t.valid AND NOT t.side_effect ORDER BY t.created_at DESC LIMIT 1",
            (run_id, tool_name, args_key))

    def add_stored_result(self, result_id, run_id, task_key, agent_id, text, read_set,
                          side_effect) -> None:
        self.execute(
            "INSERT INTO stored_results (result_id, run_id, task_key, agent_id, result_text, "
            "read_set, side_effect, valid, created_at, blob_key) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,true,%s,%s) ON CONFLICT (result_id) DO NOTHING",
            (result_id, run_id, task_key, agent_id, text, Jsonb(read_set), bool(side_effect),
             now(), self._blob(text)))

    def find_stored_result(self, run_id, task_key) -> dict | None:
        return self._one(
            "SELECT result_id, read_set, result_text FROM stored_results WHERE run_id=%s AND "
            "task_key=%s AND valid AND NOT side_effect ORDER BY created_at DESC LIMIT 1",
            (run_id, task_key))

    def tool_results_with_text(self, run_id: str) -> list[dict]:
        """Every tool result of the run with its segment text, oldest first (labels)."""
        return self.rows(
            "SELECT t.tool_result_id, t.agent_id, t.tool_name, t.args_key, t.read_set, "
            "t.created_at, s.text FROM tool_results t JOIN segments s "
            "ON s.segment_id = t.result_segment_id WHERE t.run_id=%s ORDER BY t.created_at",
            (run_id,))

    def read_sizes(self, run_id: str) -> dict[str, int]:
        """Largest known size, in tokens, of each file read in this run."""
        sizes: dict[str, int] = {}
        for r in self.rows(
                "SELECT t.read_set, s.tokens FROM tool_results t JOIN segments s "
                "ON s.segment_id = t.result_segment_id WHERE t.run_id=%s AND "
                "t.tool_name='sys_os_read'", (run_id,)):
            for path in (r["read_set"] or {}):
                if path != WORKSPACE:
                    path = os.path.normpath(path)
                    sizes[path] = max(sizes.get(path, 0), r["tokens"])
        return sizes

    def count_tool_results(self, run_id: str) -> int:
        return self._one("SELECT COUNT(*) AS n FROM tool_results WHERE run_id=%s", (run_id,))["n"]

    # -- snapshot ----------------------------------------------------------------------

    def snapshot(self, run_id: str) -> Snapshot:
        """The run's state, read together in one REPEATABLE READ transaction."""
        with self.lock, self.conn.transaction():
            self.conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            run = self.get_run(run_id)
            agents = self.rows("SELECT * FROM agents WHERE run_id=%s ORDER BY created_at", (run_id,))
            windows = {}
            for a in agents:
                windows[a["agent_id"]] = [r["segment_id"] for r in self.rows(
                    "SELECT segment_id FROM window_entries WHERE agent_id=%s AND in_window "
                    "ORDER BY position", (a["agent_id"],))]
            return Snapshot(
                run=run or {}, versions=self.current_versions(run_id), agents=agents,
                windows=windows,
                valid_tool_results=self.rows(
                    "SELECT tool_result_id, tool_name, args_key, read_set FROM tool_results "
                    "WHERE run_id=%s AND valid ORDER BY created_at", (run_id,)),
                valid_stored_results=self.rows(
                    "SELECT result_id, task_key, read_set FROM stored_results "
                    "WHERE run_id=%s AND valid ORDER BY created_at", (run_id,)),
                taken_at=now().isoformat())

    # -- decisions ---------------------------------------------------------------------

    def add_decision(self, run_id, agent_id, d: Decision, mode, decision_ms, error=None,
                     cache_predicted=None, manifest_hash=None) -> None:
        intercept = d.intercept.value if hasattr(d.intercept, "value") else d.intercept
        self.execute(
            "INSERT INTO decisions (decision_id, run_id, agent_id, intercept, mode, candidates, "
            "feasible, chosen, applied, predicted_cost, why_not, cache_predicted, manifest_hash, "
            "decision_ms, error, created_at, prediction, subject_id, payloads) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (decision_id) DO NOTHING",
            (d.id, run_id, agent_id, intercept, mode,
             Jsonb({k: asdict(v) for k, v in d.candidate_costs.items()}), Jsonb(list(d.feasible)),
             d.chosen.name, bool(d.applied), Jsonb(asdict(d.cost)),
             Jsonb([asdict(r) for r in d.rejected]), cache_predicted, manifest_hash, decision_ms,
             error, now(), None if d.prediction is None else Jsonb(d.prediction), d.subject_id,
             Jsonb(json.loads(json.dumps(d.payloads, default=str)))))

    def decisions_of(self, run_id: str) -> list[dict]:
        return self.rows("SELECT * FROM decisions WHERE run_id=%s ORDER BY created_at", (run_id,))

    def decision_summary(self, run_id: str) -> dict:
        """Counts per (intercept, chosen, applied), and how often each candidate was feasible."""
        by_choice = self.rows(
            "SELECT intercept, chosen, applied, COUNT(*) AS n FROM decisions WHERE run_id=%s "
            "GROUP BY intercept, chosen, applied ORDER BY intercept, chosen, applied", (run_id,))
        feasible = self.rows(
            "SELECT f.name, COUNT(*) AS n FROM decisions d, jsonb_array_elements_text(d.feasible) "
            "AS f(name) WHERE d.run_id=%s GROUP BY f.name ORDER BY f.name", (run_id,))
        return dict(by_choice=by_choice, feasible_counts={r["name"]: r["n"] for r in feasible})

    def replay_rows(self, run_id: str) -> list[dict]:
        """admit_tool_result decisions joined to their labels: what replay scores against."""
        return self.rows(
            "SELECT d.decision_id, d.subject_id, d.agent_id, d.candidates, d.predicted_cost, "
            "d.payloads, h.h_actual, l.tool_name, l.arrived_call, l.needed_calls "
            "FROM decisions d JOIN labels h ON h.run_id = d.run_id AND h.kind='decision' "
            "AND h.subject_id = d.decision_id JOIN labels l ON l.run_id = d.run_id AND "
            "l.kind='result' AND l.subject_id = d.subject_id "
            "WHERE d.run_id=%s AND d.intercept='admit_tool_result' ORDER BY d.created_at",
            (run_id,))

    # -- outcomes ----------------------------------------------------------------------

    def add_outcome(self, outcome_id, run_id, agent_id, decision_id, phase, u: ProviderUsage,
                    cost_nu, cost_usd, complete, period) -> None:
        self.execute(
            "INSERT INTO outcomes (outcome_id, run_id, agent_id, decision_id, phase, uncached_input, "
            "cache_read, cache_write, cache_write_1h, output, reasoning, latency_ms, cost_nu, "
            "cost_usd, cost_complete, price_period, raw, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (outcome_id) DO NOTHING",
            (outcome_id, run_id, agent_id, decision_id, phase, u.uncached_input, u.cache_read,
             u.cache_write, getattr(u, "cache_write_1h", None), u.output, u.reasoning,
             u.latency_ms, cost_nu, cost_usd, bool(complete), period,
             Jsonb(json.loads(json.dumps(u.raw, default=str))), now()))

    def outcomes_of(self, run_id: str) -> list[dict]:
        return self.rows("SELECT * FROM outcomes WHERE run_id=%s ORDER BY created_at", (run_id,))

    def outcome_totals(self, run_id: str) -> dict:
        """The run's token, cost and timing totals. NULL stays NULL: not reported, never zero."""
        return self._one(
            "SELECT COUNT(*) AS calls, SUM(uncached_input) AS uncached_input, "
            "SUM(cache_read) AS cache_read, SUM(cache_write) AS cache_write, SUM(output) AS output, "
            "SUM(reasoning) AS reasoning, SUM(cost_nu) AS cost_nu, SUM(cost_usd) AS cost_usd, "
            "COUNT(*) FILTER (WHERE phase='compaction') AS compaction_calls, "
            "COUNT(*) FILTER (WHERE NOT cost_complete) AS incomplete_calls, "
            "SUM(latency_ms) AS model_latency_ms FROM outcomes WHERE run_id=%s", (run_id,))

    def calls_by_agent(self, run_id: str) -> list[dict]:
        return self.rows(
            "SELECT agent_id, COUNT(*) AS calls, SUM(cost_nu) AS cost_nu, SUM(cost_usd) AS cost_usd, "
            "MAX(created_at) AS last FROM outcomes WHERE run_id=%s GROUP BY agent_id "
            "ORDER BY agent_id", (run_id,))

    def worker_calls(self, run_id: str) -> list[dict]:
        """Per worker (every agent but the root): calls so far and when the last one was."""
        return self.rows(
            "SELECT agent_id, MAX(created_at) AS last, COUNT(*) AS calls FROM outcomes "
            "WHERE run_id=%s AND agent_id NOT LIKE '%%:root' GROUP BY agent_id ORDER BY agent_id",
            (run_id,))

    def calls_so_far(self, agent_id: str) -> int:
        return self._one("SELECT COUNT(*) AS n FROM outcomes WHERE agent_id=%s", (agent_id,))["n"]

    def call_count_at(self, agent_id: str, segment_id: str) -> int:
        """Outcomes of the agent strictly before the segment's created_at."""
        return self._one(
            "SELECT COUNT(*) AS n FROM outcomes o, segments s WHERE s.segment_id=%s AND "
            "o.agent_id=%s AND o.created_at < s.created_at", (segment_id, agent_id))["n"]

    def cache_share(self, run_id: str) -> float | None:
        r = self._one("SELECT COALESCE(SUM(uncached_input),0) AS u, COALESCE(SUM(cache_read),0) AS c "
                      "FROM outcomes WHERE run_id=%s", (run_id,))
        return r["c"] / (r["u"] + r["c"]) if r["u"] + r["c"] else None

    def predicted_vs_actual(self, run_id: str) -> list[dict]:
        """Each outcome that had a decision, with that decision's predicted cost."""
        return self.rows(
            "SELECT o.outcome_id, o.decision_id, d.predicted_cost, o.cost_nu, o.cost_usd "
            "FROM outcomes o JOIN decisions d ON d.decision_id = o.decision_id "
            "WHERE o.run_id=%s ORDER BY o.created_at", (run_id,))

    def last_prompt_tokens(self, agent_id: str) -> int | None:
        r = self._one("SELECT COALESCE(uncached_input,0)+COALESCE(cache_read,0) AS p FROM outcomes "
                      "WHERE agent_id=%s ORDER BY created_at DESC LIMIT 1", (agent_id,))
        return r["p"] if r else None

    def input_tokens_since(self, time) -> int:
        return self._one(
            "SELECT COALESCE(SUM(COALESCE(uncached_input,0)+COALESCE(cache_read,0)),0) AS n "
            "FROM outcomes WHERE created_at >= %s::timestamptz", (time,))["n"]

    # -- runtime spans -----------------------------------------------------------------

    def add_runtime_span(self, span_id: str, run_id: str, agent_id: str, kind: str, name: str,
                         native_id: str | None, decision_id: str | None,
                         metadata: dict | None = None) -> None:
        self.execute(
            "INSERT INTO runtime_spans (span_id, run_id, agent_id, kind, name, native_id, "
            "decision_id, started_at, status, metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'open',%s) "
            "ON CONFLICT (span_id) DO NOTHING",
            (span_id, run_id, agent_id, kind, name, native_id, decision_id, now(),
             Jsonb(metadata or {})))

    def finish_runtime_span(self, span_id: str, duration_ms: float, status: str,
                            metadata: dict | None = None) -> None:
        """Close a span; `metadata` (what was learned only at the end) is merged into its own."""
        self.execute(
            "UPDATE runtime_spans SET ended_at=%s, duration_ms=%s, status=%s, "
            "metadata = metadata || %s WHERE span_id=%s",
            (now(), duration_ms, status, Jsonb(metadata or {}), span_id))

    # -- evidence ----------------------------------------------------------------------

    def add_evidence_events(self, run_id: str, agent_id: str, call_no: int, events) -> None:
        """Atomic: each acquired evidence row once (ignored if it exists), every event with
        the next `seq` of the run."""
        with self.lock, self.conn.transaction():
            seq = self.conn.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 AS n FROM evidence_events WHERE run_id=%s",
                (run_id,)).fetchone()["n"]
            t = now()
            for e in events:
                if e.ref is not None:
                    r = e.ref
                    self.conn.execute(
                        "INSERT INTO evidence VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (run_id, evidence_id) DO NOTHING",
                        (run_id, r.evidence_id, r.source_kind, r.source_key, r.source_version,
                         r.range, r.content_hash, r.byte_size, r.token_size, bool(r.recoverable), t))
                self.conn.execute(
                    "INSERT INTO evidence_events VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (run_id, seq, agent_id, call_no, e.event, e.ref.evidence_id if e.ref else None,
                     e.source_key, e.tool_name, e.args_key, t))
                seq += 1

    def mutations(self, run_id: str) -> int:
        """Workspace changes seen so far in the run: the epoch search evidence depends on."""
        return self._one("SELECT COUNT(*) AS n FROM evidence_events WHERE run_id=%s AND "
                         "event='mutated'", (run_id,))["n"]

    def evidence_of(self, run_id: str) -> list[dict]:
        """Every piece of evidence the run acquired, oldest first."""
        return self.rows("SELECT * FROM evidence WHERE run_id=%s ORDER BY created_at, evidence_id",
                         (run_id,))

    def evidence_events_of(self, run_id: str) -> list[dict]:
        return self.rows("SELECT * FROM evidence_events WHERE run_id=%s ORDER BY seq", (run_id,))

    def spans_of(self, run_id: str, kind: str | None = None) -> list[dict]:
        if kind is None:
            return self.rows("SELECT * FROM runtime_spans WHERE run_id=%s ORDER BY started_at",
                             (run_id,))
        return self.rows("SELECT * FROM runtime_spans WHERE run_id=%s AND kind=%s "
                         "ORDER BY started_at", (run_id, kind))

    def has_open_model_span(self, agent_id: str) -> bool:
        return self._one("SELECT 1 AS x FROM runtime_spans WHERE agent_id=%s AND kind='model' "
                         "AND status='open' LIMIT 1", (agent_id,)) is not None

    def count_model_spans(self, run_id: str) -> int:
        return self._one("SELECT COUNT(*) AS n FROM runtime_spans WHERE run_id=%s AND kind='model'",
                         (run_id,))["n"]

    # -- labels, pointers --------------------------------------------------------------

    def replace_labels(self, run_id: str, rows: list[dict]) -> None:
        """Atomic: delete the run's labels, insert these."""
        with self.lock, self.conn.transaction():
            self.conn.execute("DELETE FROM labels WHERE run_id=%s", (run_id,))
            for r in rows:
                needed = r.get("needed_calls")
                flag = lambda key: None if r.get(key) is None else bool(r[key])  # noqa: E731
                self.conn.execute(
                    "INSERT INTO labels (run_id, kind, subject_id, agent_id, tool_name, h_actual, "
                    "arrived_call, needed_calls, refetched, referenced, reacquired_same_version, "
                    "reacquired_after_mutation) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (run_id, r["kind"], r["subject_id"], r.get("agent_id"), r.get("tool_name"),
                     r.get("h_actual"), r.get("arrived_call"),
                     None if needed is None else Jsonb(json.loads(needed) if isinstance(needed, str)
                                                       else needed),
                     flag("refetched"), flag("referenced"), flag("reacquired_same_version"),
                     flag("reacquired_after_mutation")))

    def result_labels(self, run_id: str) -> list[dict]:
        return self.rows("SELECT agent_id, tool_name, arrived_call, needed_calls, refetched, "
                         "referenced, subject_id FROM labels WHERE run_id=%s AND kind='result' "
                         "ORDER BY subject_id", (run_id,))

    def labels_of(self, run_id: str, kind: str | None = None) -> list[dict]:
        """All of a run's labels, or those of one kind (decision | result | evidence)."""
        if kind is None:
            return self.rows("SELECT * FROM labels WHERE run_id=%s ORDER BY kind, subject_id",
                             (run_id,))
        return self.rows("SELECT * FROM labels WHERE run_id=%s AND kind=%s ORDER BY subject_id",
                         (run_id, kind))

    def add_gateway_pointer(self, run_id: str, agent_id: str, tool_call_id: str, text: str) -> None:
        self.execute(
            "INSERT INTO gateway_pointers VALUES (%s,%s,%s,%s) ON CONFLICT (run_id, agent_id, "
            "tool_call_id) DO UPDATE SET text=excluded.text", (run_id, agent_id, tool_call_id, text))

    def gateway_pointers(self, run_id: str, agent_id: str) -> dict[str, str]:
        rows = self.rows("SELECT tool_call_id, text FROM gateway_pointers WHERE run_id=%s "
                         "AND agent_id=%s", (run_id, agent_id))
        return {r["tool_call_id"]: r["text"] for r in rows}


def row_to_segment(r: dict) -> Segment:
    return Segment(
        id=r["segment_id"], run_id=r["run_id"], agent_id=r["agent_id"], native_id=r["native_id"],
        kind=SegmentKind(r["kind"]), text=r["text"], tokens=r["tokens"],
        content_hash=r["content_hash"], source=r["source"], version=r["version"],
        pinned=bool(r["pinned"]), needs_exact_bytes=bool(r["needs_exact_bytes"]),
        pair_id=r["pair_id"], role=r["role"],
        zone=Zone(r["zone"]) if "zone" in r else Zone.WARM,
        representation=Representation(r["representation"]) if "representation" in r
        else Representation.FULL,
        position=r["position"] if "position" in r else 0,
        in_window=bool(r["in_window"]) if "in_window" in r else True)
