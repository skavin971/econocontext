"""Neo4jAgentDB: Part B of AGENT_DB_CONTRACT.md on a Neo4j graph.

The same operations as PostgresAgentDB, with the data held as a graph. What is a
foreign key in Part A becomes a relationship here, and the read sets that drive
staleness become edges to Source nodes, so "what depends on this file" is a one-hop
match instead of a scan.

  (:Agent)-[:IN_RUN]->(:Run)            (:Agent)-[:CHILD_OF]->(:Agent)
  (:Segment)-[:SEEN_BY]->(:Agent)       (:Agent)-[:WINDOW {position, in_window, ...}]->(:Segment)
  (:ToolResult)-[:RESULT_OF]->(:Segment)
  (:ToolResult|:StoredResult)-[:READ {version}]->(:Source {run_id, source, version})
  (:Outcome)-[:FOR_DECISION]->(:Decision)

Conventions, as for Postgres: rows come back as dicts, timestamps as ISO-8601 strings
(a fixed microsecond format so they sort as text), JSON decoded. A property Neo4j
cannot hold (a nested map) is stored as JSON text and decoded on read. A missing
property reads as None, which keeps "NULL means not reported".
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import asdict
from datetime import datetime, timezone

from neo4j import GraphDatabase

from econocontext.types import (AgentNode, Decision, ProviderUsage, Representation, Segment,
                                SegmentKind, Zone)

from .postgres import Snapshot, read_paths

WORKSPACE = "*"
BLOB_MIN_BYTES = 4096
SEP = "\x1f"

SCHEMA = [
    "CREATE CONSTRAINT run_id IF NOT EXISTS FOR (n:Run) REQUIRE n.run_id IS UNIQUE",
    "CREATE CONSTRAINT agent_id IF NOT EXISTS FOR (n:Agent) REQUIRE n.agent_id IS UNIQUE",
    "CREATE CONSTRAINT segment_id IF NOT EXISTS FOR (n:Segment) REQUIRE n.segment_id IS UNIQUE",
    "CREATE CONSTRAINT blob_key IF NOT EXISTS FOR (n:Blob) REQUIRE n.blob_key IS UNIQUE",
    "CREATE CONSTRAINT source_key IF NOT EXISTS FOR (n:Source) REQUIRE n.key IS UNIQUE",
    "CREATE CONSTRAINT tool_result_id IF NOT EXISTS FOR (n:ToolResult) REQUIRE n.tool_result_id IS UNIQUE",
    "CREATE CONSTRAINT result_id IF NOT EXISTS FOR (n:StoredResult) REQUIRE n.result_id IS UNIQUE",
    "CREATE CONSTRAINT decision_id IF NOT EXISTS FOR (n:Decision) REQUIRE n.decision_id IS UNIQUE",
    "CREATE CONSTRAINT outcome_id IF NOT EXISTS FOR (n:Outcome) REQUIRE n.outcome_id IS UNIQUE",
    "CREATE CONSTRAINT span_id IF NOT EXISTS FOR (n:Span) REQUIRE n.span_id IS UNIQUE",
    "CREATE CONSTRAINT label_key IF NOT EXISTS FOR (n:Label) REQUIRE n.key IS UNIQUE",
    "CREATE CONSTRAINT pointer_key IF NOT EXISTS FOR (n:GatewayPointer) REQUIRE n.key IS UNIQUE",
    "CREATE CONSTRAINT evidence_key IF NOT EXISTS FOR (n:Evidence) REQUIRE n.key IS UNIQUE",
    "CREATE CONSTRAINT evidence_event_key IF NOT EXISTS FOR (n:EvidenceEvent) REQUIRE n.key IS UNIQUE",
    "CREATE INDEX evidence_source IF NOT EXISTS FOR (n:Evidence) ON (n.run_id, n.source_key)",
    "CREATE INDEX evidence_version IF NOT EXISTS FOR (n:Evidence) ON (n.source_version)",
    "CREATE INDEX evidence_event_run IF NOT EXISTS FOR (n:EvidenceEvent) ON (n.run_id, n.seq)",
    "CREATE INDEX segment_run_source IF NOT EXISTS FOR (n:Segment) ON (n.run_id, n.source)",
    "CREATE INDEX segment_agent_kind IF NOT EXISTS FOR (n:Segment) ON (n.agent_id, n.kind)",
    "CREATE INDEX tool_result_key IF NOT EXISTS FOR (n:ToolResult) ON (n.run_id, n.tool_name, n.args_key)",
    "CREATE INDEX stored_result_key IF NOT EXISTS FOR (n:StoredResult) ON (n.run_id, n.task_key)",
    "CREATE INDEX decision_run IF NOT EXISTS FOR (n:Decision) ON (n.run_id, n.intercept)",
    "CREATE INDEX outcome_run IF NOT EXISTS FOR (n:Outcome) ON (n.run_id, n.agent_id)",
    "CREATE INDEX outcome_agent_time IF NOT EXISTS FOR (n:Outcome) ON (n.agent_id, n.created_at)",
    "CREATE INDEX outcome_time IF NOT EXISTS FOR (n:Outcome) ON (n.created_at)",
    "CREATE INDEX span_run IF NOT EXISTS FOR (n:Span) ON (n.run_id, n.started_at)",
    "CREATE INDEX span_open IF NOT EXISTS FOR (n:Span) ON (n.run_id, n.status, n.agent_id)",
    "CREATE INDEX label_run IF NOT EXISTS FOR (n:Label) ON (n.run_id, n.kind)",
    # Ranked full text is available as a Lucene index; search_segments uses CONTAINS
    # scoring so results are consistent immediately after a write (the index is async).
    "CREATE FULLTEXT INDEX segment_text IF NOT EXISTS FOR (n:Segment) ON EACH [n.text]",
]

# Part A's columns per record, so a property never set comes back as None ("not reported").
COLUMNS = {
    "Run": ["run_id", "host", "instance_id", "arm", "mode", "model", "temperature",
            "config_fingerprint", "started_at", "ended_at", "status", "resolved", "jev",
            "overrides", "workdir"],
    "Agent": ["agent_id", "run_id", "parent_id", "subagent_type", "status", "window_max_tokens",
              "created_at", "updated_at"],
    "Decision": ["decision_id", "run_id", "agent_id", "intercept", "mode", "candidates", "feasible",
                 "chosen", "applied", "predicted_cost", "why_not", "cache_predicted",
                 "manifest_hash", "decision_ms", "error", "created_at", "prediction", "subject_id",
                 "payloads"],
    "Outcome": ["outcome_id", "run_id", "agent_id", "decision_id", "phase", "uncached_input",
                "cache_read", "cache_write", "cache_write_1h", "output", "reasoning", "latency_ms",
                "cost_nu", "cost_usd", "cost_complete", "price_period", "raw", "created_at"],
    "Evidence": ["run_id", "evidence_id", "source_kind", "source_key", "source_version", "range",
                 "content_hash", "byte_size", "token_size", "recoverable", "created_at"],
    "EvidenceEvent": ["run_id", "seq", "agent_id", "call_no", "event", "evidence_id", "source_key",
                      "tool_name", "args_key", "created_at"],
    "Label": ["run_id", "kind", "subject_id", "agent_id", "tool_name", "h_actual", "arrived_call",
              "needed_calls", "refetched", "referenced", "reacquired_same_version",
              "reacquired_after_mutation"],
    "Span": ["span_id", "run_id", "agent_id", "kind", "name", "native_id", "decision_id",
             "started_at", "ended_at", "duration_ms", "status", "metadata"],
}

JSON_PROPS = {"overrides", "read_set", "candidates", "feasible", "predicted_cost", "why_not",
              "prediction", "payloads", "raw", "metadata", "needed_calls"}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _j(value):
    return None if value is None else json.dumps(value, sort_keys=True, default=str)


def _decode(row: dict, label: str | None = None) -> dict:
    out = {k: (json.loads(v) if k in JSON_PROPS and isinstance(v, str) else v)
           for k, v in row.items()}
    for column in COLUMNS.get(label, ()):
        out.setdefault(column, None)
    return out


class Neo4jBlobStore:
    def __init__(self, db: "Neo4jAgentDB"):
        self.db = db

    def put(self, data: bytes) -> str:
        key = hashlib.sha256(data).hexdigest()
        self.db._write("MERGE (b:Blob {blob_key: $key}) ON CREATE SET b.size = $size, "
                       "b.data = $data, b.created_at = $t", key=key, size=len(data),
                       data=bytes(data), t=now())
        return key

    def get(self, blob_key: str) -> bytes:
        rows = self.db._read("MATCH (b:Blob {blob_key: $key}) RETURN b.data AS data", key=blob_key)
        if not rows:
            raise KeyError(blob_key)
        return bytes(rows[0]["data"])


class Neo4jAgentDB:
    def __init__(self, uri: str | None = None, auth: tuple[str, str] | None = None,
                 database: str | None = None, blob_min_bytes: int = BLOB_MIN_BYTES):
        """Connects to `uri` with `auth`; both default to the environment:
        NEO4J_URI (default bolt://localhost:7687) and NEO4J_AUTH as user:password."""
        uri = uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        if auth is None and os.environ.get("NEO4J_AUTH"):
            auth = tuple(os.environ["NEO4J_AUTH"].split(":", 1))
        self.driver = GraphDatabase.driver(uri, auth=auth)
        self.database = database
        self.lock = threading.RLock()
        for statement in SCHEMA:
            self._write(statement)
        self.blobs = Neo4jBlobStore(self)
        self.blob_min_bytes = blob_min_bytes
        self.has_fts = True

    def close(self) -> None:
        self.driver.close()

    # -- plumbing ---------------------------------------------------------------------

    def _session(self):
        return self.driver.session(database=self.database)

    def _write(self, cypher: str, **params) -> list[dict]:
        with self.lock, self._session() as s:
            return s.execute_write(lambda tx: [dict(r) for r in tx.run(cypher, **params)])

    def _read(self, cypher: str, **params) -> list[dict]:
        with self.lock, self._session() as s:
            return s.execute_read(lambda tx: [dict(r) for r in tx.run(cypher, **params)])

    def _tx(self, fn):
        """Run `fn(tx)` in one write transaction."""
        with self.lock, self._session() as s:
            return s.execute_write(fn)

    def rows(self, cypher: str, params: dict | None = None) -> list[dict]:
        """Plain Cypher, for parity with the reference's plain SQL. Not part of the contract."""
        return [_decode(r) for r in self._read(cypher, **(params or {}))]

    def execute(self, cypher: str, params: dict | None = None) -> None:
        self._write(cypher, **(params or {}))

    def _nodes(self, cypher: str, label: str | None = None, **params) -> list[dict]:
        """Rows whose single column is a node: decoded property dicts, every column present."""
        return [_decode(dict(next(iter(r.values()))), label) for r in self._read(cypher, **params)]

    # -- runs -------------------------------------------------------------------------

    def start_run(self, run_id, host, instance_id, arm, mode, model, temperature, fingerprint,
                  jev: bool = False, overrides: dict | None = None, workdir: str | None = None):
        self._write(
            "MERGE (r:Run {run_id: $run_id}) ON CREATE SET r.host = $host, "
            "r.instance_id = $instance_id, r.arm = $arm, r.mode = $mode, r.model = $model, "
            "r.temperature = $temperature, r.config_fingerprint = $fp, r.started_at = $t, "
            "r.jev = $jev, r.overrides = $overrides, r.workdir = $workdir",
            run_id=run_id, host=host, instance_id=instance_id, arm=arm, mode=mode, model=model,
            temperature=float(temperature), fp=fingerprint, t=now(), jev=bool(jev),
            overrides=_j(overrides) if overrides else None, workdir=workdir)

    def end_run(self, run_id: str, status: str) -> None:
        self._write("MATCH (r:Run {run_id: $run_id}) SET r.ended_at = $t, r.status = $status",
                    run_id=run_id, t=now(), status=status)

    def set_resolved(self, run_id: str, resolved: bool) -> None:
        self._write("MATCH (r:Run {run_id: $run_id}) SET r.resolved = $v", run_id=run_id,
                    v=bool(resolved))

    def get_run(self, run_id: str) -> dict | None:
        rows = self._nodes("MATCH (r:Run {run_id: $run_id}) RETURN r", "Run", run_id=run_id)
        return rows[0] if rows else None

    def list_runs(self, label: str) -> list[dict]:
        return self._nodes("MATCH (r:Run) WHERE r.run_id STARTS WITH $p RETURN r ORDER BY r.run_id",
                           "Run", p=f"{label}:")

    def labelled_runs(self, exclude_instance: str | None = None) -> list[str]:
        rows = self._read(
            "MATCH (l:Label) WITH DISTINCT l.run_id AS run_id MATCH (r:Run {run_id: run_id}) "
            "WHERE $ex IS NULL OR r.instance_id IS NULL OR r.instance_id <> $ex "
            "RETURN run_id ORDER BY run_id", ex=exclude_instance)
        return [r["run_id"] for r in rows]

    # -- agents, segments, windows ------------------------------------------------------

    def upsert_agent(self, run_id: str, node: AgentNode) -> None:
        status = node.status.value if hasattr(node.status, "value") else node.status
        self._write(
            "MATCH (r:Run {run_id: $run_id}) MERGE (a:Agent {agent_id: $agent_id}) "
            "ON CREATE SET a.run_id = $run_id, a.parent_id = $parent_id, "
            "a.subagent_type = $subagent_type, a.window_max_tokens = $wmt, a.created_at = $t "
            "SET a.status = $status, a.updated_at = $t MERGE (a)-[:IN_RUN]->(r) "
            "WITH a WHERE $parent_id IS NOT NULL MATCH (p:Agent {agent_id: $parent_id}) "
            "MERGE (a)-[:CHILD_OF]->(p)",
            run_id=run_id, agent_id=node.agent_id, parent_id=node.parent_id,
            subagent_type=node.subagent_type, wmt=node.window_max_tokens, status=status, t=now())

    def _blob(self, text: str) -> str | None:
        data = text.encode()
        return self.blobs.put(data) if len(data) >= self.blob_min_bytes else None

    def add_segment(self, s: Segment) -> None:
        """Write-once: MERGE on the identity, properties set only on create."""
        kind = s.kind.value if hasattr(s.kind, "value") else s.kind
        self._write(
            "MERGE (n:Segment {segment_id: $id}) ON CREATE SET n.run_id = $run_id, "
            "n.agent_id = $agent_id, n.native_id = $native_id, n.kind = $kind, n.text = $text, "
            "n.tokens = $tokens, n.content_hash = $content_hash, n.source = $source, "
            "n.version = $version, n.pinned = $pinned, n.needs_exact_bytes = $needs, "
            "n.pair_id = $pair_id, n.role = $role, n.created_at = $t, n.blob_key = $blob_key "
            "WITH n MATCH (a:Agent {agent_id: $agent_id}) MERGE (n)-[:SEEN_BY]->(a)",
            id=s.id, run_id=s.run_id, agent_id=s.agent_id, native_id=s.native_id, kind=kind,
            text=s.text, tokens=s.tokens, content_hash=s.content_hash, source=s.source,
            version=s.version, pinned=bool(s.pinned), needs=bool(s.needs_exact_bytes),
            pair_id=s.pair_id, role=s.role, t=now(), blob_key=self._blob(s.text))

    def segment(self, segment_id: str) -> Segment | None:
        rows = self._nodes("MATCH (n:Segment {segment_id: $id}) RETURN n", id=segment_id)
        return row_to_segment(rows[0]) if rows else None

    def set_window(self, agent_id: str, segments: list[Segment]) -> None:
        t = now()

        def work(tx):
            tx.run("MATCH (a:Agent {agent_id: $agent_id})-[w:WINDOW]->() "
                   "SET w.in_window = false, w.updated_at = $t", agent_id=agent_id, t=t)
            for s in segments:
                tx.run("MATCH (a:Agent {agent_id: $agent_id}), (n:Segment {segment_id: $sid}) "
                       "MERGE (a)-[w:WINDOW]->(n) SET w.position = $position, w.in_window = true, "
                       "w.representation = $rep, w.zone = $zone, w.updated_at = $t",
                       agent_id=agent_id, sid=s.id, position=s.position,
                       rep=s.representation.value, zone=s.zone.value, t=t)
        self._tx(work)

    def window(self, agent_id: str) -> list[Segment]:
        rows = self._read(
            "MATCH (a:Agent {agent_id: $agent_id})-[w:WINDOW {in_window: true}]->(n:Segment) "
            "RETURN n, w ORDER BY w.position", agent_id=agent_id)
        return [row_to_segment({**dict(r["n"]), **dict(r["w"])}) for r in rows]

    def first_segment(self, agent_id: str, kind) -> Segment | None:
        kind = kind.value if hasattr(kind, "value") else kind
        rows = self._nodes("MATCH (n:Segment {agent_id: $agent_id, kind: $kind}) RETURN n "
                           "ORDER BY n.created_at LIMIT 1", agent_id=agent_id, kind=kind)
        return row_to_segment(rows[0]) if rows else None

    def segments_of(self, agent_id: str, kind) -> list[Segment]:
        kind = kind.value if hasattr(kind, "value") else kind
        return [row_to_segment(r) for r in self._nodes(
            "MATCH (n:Segment {agent_id: $agent_id, kind: $kind}) RETURN n ORDER BY n.created_at",
            agent_id=agent_id, kind=kind)]

    def assistant_segments(self, run_id: str) -> list[dict]:
        return self._read(
            "MATCH (n:Segment {run_id: $run_id, role: 'assistant'}) RETURN n.segment_id AS "
            "segment_id, n.agent_id AS agent_id, n.text AS text, n.created_at AS created_at "
            "ORDER BY n.created_at", run_id=run_id)

    def search_segments(self, run_id: str, terms: list[str], limit: int = 5) -> list[Segment]:
        terms = [t.lower() for t in terms if t]
        if not terms:
            return []
        rows = self._nodes(
            "MATCH (n:Segment {run_id: $run_id}) WITH n, "
            "size([t IN $terms WHERE toLower(n.text) CONTAINS t]) AS score WHERE score > 0 "
            "RETURN n ORDER BY score DESC, n.created_at DESC LIMIT $limit",
            run_id=run_id, terms=terms, limit=limit * 4)
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

    @staticmethod
    def _skey(run_id: str, source: str) -> str:
        return f"{run_id}{SEP}{source}"

    def current_versions(self, run_id: str) -> dict[str, str]:
        rows = self._read("MATCH (s:Source {run_id: $run_id}) WHERE s.version IS NOT NULL "
                          "RETURN s.source AS source, s.version AS version", run_id=run_id)
        return {r["source"]: r["version"] for r in rows}

    def bump(self, run_id: str, sources: list[str]) -> None:
        t = now()

        def work(tx):
            for source in {*sources, WORKSPACE}:
                tx.run("MERGE (s:Source {key: $key}) ON CREATE SET s.run_id = $run_id, "
                       "s.source = $source SET s.version = toString(toInteger(coalesce(s.version, "
                       "'0')) + 1), s.updated_at = $t",
                       key=self._skey(run_id, source), run_id=run_id, source=source, t=t)
            self._invalidate(tx, run_id)
        self._tx(work)

    @staticmethod
    def _invalidate(tx, run_id: str) -> None:
        for label in ("ToolResult", "StoredResult"):
            tx.run(f"MATCH (r:{label} {{run_id: $run_id}})-[e:READ]->(s:Source) "
                   f"WHERE r.valid AND e.version <> coalesce(s.version, '0') SET r.valid = false",
                   run_id=run_id)

    def invalidate(self, run_id: str) -> None:
        self._tx(lambda tx: self._invalidate(tx, run_id))

    def dependents(self, run_id: str, source: str) -> dict[str, list[str]]:
        source = source if source == WORKSPACE else os.path.normpath(source)
        key = self._skey(run_id, source)
        tool = [r["id"] for r in self._read(
            "MATCH (r:ToolResult {run_id: $run_id})-[:READ]->(:Source {key: $key}) "
            "RETURN r.tool_result_id AS id ORDER BY r.created_at", run_id=run_id, key=key)]
        stored = [r["id"] for r in self._read(
            "MATCH (r:StoredResult {run_id: $run_id})-[:READ]->(:Source {key: $key}) "
            "RETURN r.result_id AS id ORDER BY r.created_at", run_id=run_id, key=key)]
        workers = sorted({r["agent_id"] for r in self._read(
            "MATCH (n:Segment {run_id: $run_id, kind: 'tool_call'}) "
            "WHERE NOT n.agent_id ENDS WITH ':root' RETURN n.agent_id AS agent_id, n.text AS text",
            run_id=run_id) if source in read_paths(r["text"])})
        return dict(tool_results=tool, stored_results=stored, workers=workers)

    def _read_edges(self, tx, label: str, key_prop: str, row_id: str, run_id: str, read_set: dict):
        for source, version in read_set.items():
            tx.run(f"MATCH (r:{label} {{{key_prop}: $id}}) MERGE (s:Source {{key: $key}}) "
                   f"ON CREATE SET s.run_id = $run_id, s.source = $source "
                   f"MERGE (r)-[e:READ]->(s) SET e.version = $version",
                   id=row_id, key=self._skey(run_id, source), run_id=run_id, source=source,
                   version=str(version))

    def add_tool_result(self, row_id, run_id, agent_id, tool_name, args_key, segment_id,
                        read_set, side_effect) -> None:
        def work(tx):
            created = tx.run(
                "MERGE (r:ToolResult {tool_result_id: $id}) ON CREATE SET r.run_id = $run_id, "
                "r.agent_id = $agent_id, r.tool_name = $tool_name, r.args_key = $args_key, "
                "r.read_set = $read_set, r.side_effect = $side_effect, r.valid = true, "
                "r.created_at = $t, r.fresh = true "
                "WITH r, r.fresh AS fresh REMOVE r.fresh "
                "WITH r, fresh WHERE fresh MATCH (n:Segment {segment_id: $sid}) "
                "MERGE (r)-[:RESULT_OF]->(n) RETURN count(r) AS n",
                id=row_id, run_id=run_id, agent_id=agent_id, tool_name=tool_name,
                args_key=args_key, read_set=_j(read_set), side_effect=bool(side_effect), t=now(),
                sid=segment_id).single()["n"]
            if created:
                self._read_edges(tx, "ToolResult", "tool_result_id", row_id, run_id, read_set)
        self._tx(work)

    def find_tool_result(self, run_id, tool_name, args_key) -> dict | None:
        rows = self._read(
            "MATCH (r:ToolResult {run_id: $run_id, tool_name: $tool_name, args_key: $args_key, "
            "valid: true, side_effect: false})-[:RESULT_OF]->(n:Segment) "
            "RETURN r.tool_result_id AS tool_result_id, r.read_set AS read_set, n.text AS text "
            "ORDER BY r.created_at DESC LIMIT 1", run_id=run_id, tool_name=tool_name,
            args_key=args_key)
        return _decode(rows[0]) if rows else None

    def add_stored_result(self, result_id, run_id, task_key, agent_id, text, read_set,
                          side_effect) -> None:
        blob = self._blob(text)

        def work(tx):
            created = tx.run(
                "MERGE (r:StoredResult {result_id: $id}) ON CREATE SET r.run_id = $run_id, "
                "r.task_key = $task_key, r.agent_id = $agent_id, r.result_text = $text, "
                "r.read_set = $read_set, r.side_effect = $side_effect, r.valid = true, "
                "r.created_at = $t, r.blob_key = $blob_key, r.fresh = true "
                "WITH r, r.fresh AS fresh REMOVE r.fresh RETURN fresh",
                id=result_id, run_id=run_id, task_key=task_key, agent_id=agent_id, text=text,
                read_set=_j(read_set), side_effect=bool(side_effect), t=now(),
                blob_key=blob).single()["fresh"]
            if created:
                self._read_edges(tx, "StoredResult", "result_id", result_id, run_id, read_set)
        self._tx(work)

    def find_stored_result(self, run_id, task_key) -> dict | None:
        rows = self._read(
            "MATCH (r:StoredResult {run_id: $run_id, task_key: $task_key, valid: true, "
            "side_effect: false}) RETURN r.result_id AS result_id, r.read_set AS read_set, "
            "r.result_text AS result_text ORDER BY r.created_at DESC LIMIT 1",
            run_id=run_id, task_key=task_key)
        return _decode(rows[0]) if rows else None

    def tool_results_with_text(self, run_id: str) -> list[dict]:
        return [_decode(r) for r in self._read(
            "MATCH (r:ToolResult {run_id: $run_id})-[:RESULT_OF]->(n:Segment) "
            "RETURN r.tool_result_id AS tool_result_id, r.agent_id AS agent_id, "
            "r.tool_name AS tool_name, r.args_key AS args_key, r.read_set AS read_set, "
            "r.created_at AS created_at, n.text AS text ORDER BY r.created_at", run_id=run_id)]

    def read_sizes(self, run_id: str) -> dict[str, int]:
        sizes: dict[str, int] = {}
        for r in self._read(
                "MATCH (r:ToolResult {run_id: $run_id, tool_name: 'sys_os_read'})-[:RESULT_OF]->"
                "(n:Segment) RETURN r.read_set AS read_set, n.tokens AS tokens", run_id=run_id):
            for path in json.loads(r["read_set"] or "{}"):
                if path != WORKSPACE:
                    path = os.path.normpath(path)
                    sizes[path] = max(sizes.get(path, 0), r["tokens"])
        return sizes

    def count_tool_results(self, run_id: str) -> int:
        return self._read("MATCH (r:ToolResult {run_id: $run_id}) RETURN count(r) AS n",
                          run_id=run_id)[0]["n"]

    # -- snapshot ----------------------------------------------------------------------

    def snapshot(self, run_id: str) -> Snapshot:
        def work(tx):
            def rows(cypher, **p):
                return [dict(r) for r in tx.run(cypher, **p)]
            run = rows("MATCH (r:Run {run_id: $run_id}) RETURN r", run_id=run_id)
            agents = [_decode(dict(r["a"]), "Agent") for r in rows(
                "MATCH (a:Agent {run_id: $run_id}) RETURN a ORDER BY a.created_at", run_id=run_id)]
            windows = {a["agent_id"]: [r["id"] for r in rows(
                "MATCH (:Agent {agent_id: $aid})-[w:WINDOW {in_window: true}]->(n:Segment) "
                "RETURN n.segment_id AS id ORDER BY w.position", aid=a["agent_id"])]
                for a in agents}
            versions = {r["source"]: r["version"] for r in rows(
                "MATCH (s:Source {run_id: $run_id}) WHERE s.version IS NOT NULL "
                "RETURN s.source AS source, s.version AS version", run_id=run_id)}
            return Snapshot(
                run=_decode(dict(run[0]["r"]), "Run") if run else {}, versions=versions, agents=agents,
                windows=windows,
                valid_tool_results=[_decode(r) for r in rows(
                    "MATCH (r:ToolResult {run_id: $run_id, valid: true}) RETURN "
                    "r.tool_result_id AS tool_result_id, r.tool_name AS tool_name, "
                    "r.args_key AS args_key, r.read_set AS read_set ORDER BY r.created_at",
                    run_id=run_id)],
                valid_stored_results=[_decode(r) for r in rows(
                    "MATCH (r:StoredResult {run_id: $run_id, valid: true}) RETURN "
                    "r.result_id AS result_id, r.task_key AS task_key, r.read_set AS read_set "
                    "ORDER BY r.created_at", run_id=run_id)],
                taken_at=now())
        with self.lock, self._session() as s:
            return s.execute_read(work)

    # -- decisions ---------------------------------------------------------------------

    def add_decision(self, run_id, agent_id, d: Decision, mode, decision_ms, error=None,
                     cache_predicted=None, manifest_hash=None) -> None:
        intercept = d.intercept.value if hasattr(d.intercept, "value") else d.intercept
        self._write(
            "MERGE (n:Decision {decision_id: $id}) ON CREATE SET n.run_id = $run_id, "
            "n.agent_id = $agent_id, n.intercept = $intercept, n.mode = $mode, "
            "n.candidates = $candidates, n.feasible = $feasible, n.chosen = $chosen, "
            "n.applied = $applied, n.predicted_cost = $predicted_cost, n.why_not = $why_not, "
            "n.cache_predicted = $cache_predicted, n.manifest_hash = $manifest_hash, "
            "n.decision_ms = $decision_ms, n.error = $error, n.created_at = $t, "
            "n.prediction = $prediction, n.subject_id = $subject_id, n.payloads = $payloads",
            id=d.id, run_id=run_id, agent_id=agent_id, intercept=intercept, mode=mode,
            candidates=_j({k: asdict(v) for k, v in d.candidate_costs.items()}),
            feasible=_j(list(d.feasible)), chosen=d.chosen.name, applied=bool(d.applied),
            predicted_cost=_j(asdict(d.cost)), why_not=_j([asdict(r) for r in d.rejected]),
            cache_predicted=cache_predicted, manifest_hash=manifest_hash,
            decision_ms=float(decision_ms), error=error, t=now(), prediction=_j(d.prediction),
            subject_id=d.subject_id, payloads=_j(d.payloads))

    def decisions_of(self, run_id: str) -> list[dict]:
        return self._nodes("MATCH (n:Decision {run_id: $run_id}) RETURN n ORDER BY n.created_at",
                           "Decision", run_id=run_id)

    def decision_summary(self, run_id: str) -> dict:
        by_choice = self._read(
            "MATCH (n:Decision {run_id: $run_id}) RETURN n.intercept AS intercept, "
            "n.chosen AS chosen, n.applied AS applied, count(*) AS n "
            "ORDER BY intercept, chosen, applied", run_id=run_id)
        feasible: dict[str, int] = {}
        for r in self._read("MATCH (n:Decision {run_id: $run_id}) RETURN n.feasible AS f",
                            run_id=run_id):
            for name in json.loads(r["f"]):
                feasible[name] = feasible.get(name, 0) + 1
        return dict(by_choice=by_choice, feasible_counts=dict(sorted(feasible.items())))

    def replay_rows(self, run_id: str) -> list[dict]:
        return [_decode(r) for r in self._read(
            "MATCH (d:Decision {run_id: $run_id, intercept: 'admit_tool_result'}), "
            "(h:Label {run_id: $run_id, kind: 'decision', subject_id: d.decision_id}), "
            "(l:Label {run_id: $run_id, kind: 'result', subject_id: d.subject_id}) "
            "RETURN d.decision_id AS decision_id, d.subject_id AS subject_id, "
            "d.agent_id AS agent_id, d.candidates AS candidates, d.predicted_cost AS "
            "predicted_cost, d.payloads AS payloads, h.h_actual AS h_actual, "
            "l.tool_name AS tool_name, l.arrived_call AS arrived_call, "
            "l.needed_calls AS needed_calls ORDER BY d.created_at", run_id=run_id)]

    # -- outcomes ----------------------------------------------------------------------

    def add_outcome(self, outcome_id, run_id, agent_id, decision_id, phase, u: ProviderUsage,
                    cost_nu, cost_usd, complete, period) -> None:
        self._write(
            "MERGE (n:Outcome {outcome_id: $id}) ON CREATE SET n.run_id = $run_id, "
            "n.agent_id = $agent_id, n.decision_id = $decision_id, n.phase = $phase, "
            "n.uncached_input = $ui, n.cache_read = $cr, n.cache_write = $cw, "
            "n.cache_write_1h = $cw1h, n.output = $out, "
            "n.reasoning = $reasoning, n.latency_ms = $latency, n.cost_nu = $cost_nu, "
            "n.cost_usd = $cost_usd, n.cost_complete = $complete, n.price_period = $period, "
            "n.raw = $raw, n.created_at = $t "
            "WITH n WHERE $decision_id IS NOT NULL MATCH (d:Decision {decision_id: $decision_id}) "
            "MERGE (n)-[:FOR_DECISION]->(d)",
            id=outcome_id, run_id=run_id, agent_id=agent_id, decision_id=decision_id, phase=phase,
            ui=u.uncached_input, cr=u.cache_read, cw=u.cache_write,
            cw1h=getattr(u, "cache_write_1h", None), out=u.output,
            reasoning=u.reasoning, latency=u.latency_ms, cost_nu=cost_nu, cost_usd=cost_usd,
            complete=bool(complete), period=period, raw=_j(u.raw), t=now())

    def outcomes_of(self, run_id: str) -> list[dict]:
        return self._nodes("MATCH (n:Outcome {run_id: $run_id}) RETURN n ORDER BY n.created_at",
                           "Outcome", run_id=run_id)

    def outcome_totals(self, run_id: str) -> dict:
        rows = self.outcomes_of(run_id)

        def total(key):
            values = [r.get(key) for r in rows if r.get(key) is not None]
            return sum(values) if values else None
        return dict(calls=len(rows), uncached_input=total("uncached_input"),
                    cache_read=total("cache_read"), cache_write=total("cache_write"),
                    output=total("output"), reasoning=total("reasoning"),
                    cost_nu=total("cost_nu"), cost_usd=total("cost_usd"),
                    compaction_calls=sum(r.get("phase") == "compaction" for r in rows),
                    incomplete_calls=sum(not r.get("cost_complete") for r in rows),
                    model_latency_ms=total("latency_ms"))

    def calls_by_agent(self, run_id: str) -> list[dict]:
        return self._read(
            "MATCH (n:Outcome {run_id: $run_id}) RETURN n.agent_id AS agent_id, "
            "count(*) AS calls, sum(n.cost_nu) AS cost_nu, sum(n.cost_usd) AS cost_usd, "
            "max(n.created_at) AS last ORDER BY agent_id", run_id=run_id)

    def worker_calls(self, run_id: str) -> list[dict]:
        return self._read(
            "MATCH (n:Outcome {run_id: $run_id}) WHERE NOT n.agent_id ENDS WITH ':root' "
            "RETURN n.agent_id AS agent_id, max(n.created_at) AS last, count(*) AS calls "
            "ORDER BY agent_id", run_id=run_id)

    def calls_so_far(self, agent_id: str) -> int:
        return self._read("MATCH (n:Outcome {agent_id: $a}) RETURN count(n) AS n",
                          a=agent_id)[0]["n"]

    def call_count_at(self, agent_id: str, segment_id: str) -> int:
        return self._read(
            "MATCH (s:Segment {segment_id: $sid}) MATCH (o:Outcome {agent_id: $a}) "
            "WHERE o.created_at < s.created_at RETURN count(o) AS n",
            sid=segment_id, a=agent_id)[0]["n"]

    def cache_share(self, run_id: str) -> float | None:
        r = self._read("MATCH (n:Outcome {run_id: $run_id}) RETURN "
                       "sum(coalesce(n.uncached_input, 0)) AS u, sum(coalesce(n.cache_read, 0)) AS c",
                       run_id=run_id)[0]
        return r["c"] / (r["u"] + r["c"]) if r["u"] + r["c"] else None

    def predicted_vs_actual(self, run_id: str) -> list[dict]:
        return [_decode(r) for r in self._read(
            "MATCH (o:Outcome {run_id: $run_id})-[:FOR_DECISION]->(d:Decision) RETURN "
            "o.outcome_id AS outcome_id, o.decision_id AS decision_id, "
            "d.predicted_cost AS predicted_cost, o.cost_nu AS cost_nu, o.cost_usd AS cost_usd "
            "ORDER BY o.created_at", run_id=run_id)]

    def last_prompt_tokens(self, agent_id: str) -> int | None:
        rows = self._read("MATCH (n:Outcome {agent_id: $a}) RETURN coalesce(n.uncached_input, 0) + "
                          "coalesce(n.cache_read, 0) AS p ORDER BY n.created_at DESC LIMIT 1",
                          a=agent_id)
        return rows[0]["p"] if rows else None

    def input_tokens_since(self, time) -> int:
        time = time.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00") if isinstance(time, datetime) else time
        return self._read("MATCH (n:Outcome) WHERE n.created_at >= $t RETURN "
                          "sum(coalesce(n.uncached_input, 0) + coalesce(n.cache_read, 0)) AS n",
                          t=time)[0]["n"]

    # -- runtime spans -----------------------------------------------------------------

    def add_runtime_span(self, span_id: str, run_id: str, agent_id: str, kind: str, name: str,
                         native_id: str | None, decision_id: str | None,
                         metadata: dict | None = None) -> None:
        self._write(
            "MERGE (n:Span {span_id: $id}) ON CREATE SET n.run_id = $run_id, "
            "n.agent_id = $agent_id, n.kind = $kind, n.name = $name, n.native_id = $native_id, "
            "n.decision_id = $decision_id, n.started_at = $t, n.status = 'open', "
            "n.metadata = $metadata", id=span_id, run_id=run_id, agent_id=agent_id, kind=kind,
            name=name, native_id=native_id, decision_id=decision_id, t=now(),
            metadata=_j(metadata or {}))

    def finish_runtime_span(self, span_id: str, duration_ms: float, status: str,
                            metadata: dict | None = None) -> None:
        """Close a span; `metadata` is merged into what the span already holds."""
        def work(tx):
            if metadata:
                row = tx.run("MATCH (n:Span {span_id: $id}) RETURN n.metadata AS m",
                             id=span_id).single()
                merged = {**json.loads((row and row["m"]) or "{}"), **metadata}
                tx.run("MATCH (n:Span {span_id: $id}) SET n.metadata = $m", id=span_id,
                       m=_j(merged))
            tx.run("MATCH (n:Span {span_id: $id}) SET n.ended_at = $t, n.duration_ms = $d, "
                   "n.status = $s", id=span_id, t=now(), d=float(duration_ms), s=status)
        self._tx(work)

    # -- evidence ----------------------------------------------------------------------

    def add_evidence_events(self, run_id: str, agent_id: str, call_no: int, events) -> None:
        """Atomic: each acquired evidence node once, every event as the run's next `seq`,
        linked to its evidence when it has one."""
        t = now()

        def work(tx):
            seq = tx.run("MATCH (e:EvidenceEvent {run_id: $run_id}) RETURN coalesce(max(e.seq), -1) "
                         "+ 1 AS n", run_id=run_id).single()["n"]
            for e in events:
                if e.ref is not None:
                    r = e.ref
                    tx.run("MERGE (v:Evidence {key: $key}) ON CREATE SET v.run_id = $run_id, "
                           "v.evidence_id = $eid, v.source_kind = $kind, v.source_key = $skey, "
                           "v.source_version = $ver, v.range = $range, v.content_hash = $ch, "
                           "v.byte_size = $bytes, v.token_size = $tokens, "
                           "v.recoverable = $recoverable, v.created_at = $t",
                           key=f"{run_id}{SEP}{r.evidence_id}", run_id=run_id, eid=r.evidence_id,
                           kind=r.source_kind, skey=r.source_key, ver=r.source_version,
                           range=r.range, ch=r.content_hash, bytes=r.byte_size,
                           tokens=r.token_size, recoverable=bool(r.recoverable), t=t)
                tx.run("CREATE (ev:EvidenceEvent {key: $key, run_id: $run_id, seq: $seq, "
                       "agent_id: $agent_id, call_no: $call_no, event: $event, "
                       "evidence_id: $eid, source_key: $skey, tool_name: $tool, args_key: $args, "
                       "created_at: $t}) WITH ev WHERE $eid IS NOT NULL "
                       "MATCH (v:Evidence {key: $vkey}) MERGE (ev)-[:OF_EVIDENCE]->(v)",
                       key=f"{run_id}{SEP}{seq}", run_id=run_id, seq=seq, agent_id=agent_id,
                       call_no=call_no, event=e.event,
                       eid=e.ref.evidence_id if e.ref else None, skey=e.source_key,
                       tool=e.tool_name, args=e.args_key, t=t,
                       vkey=f"{run_id}{SEP}{e.ref.evidence_id}" if e.ref else "")
                seq += 1
        self._tx(work)

    def mutations(self, run_id: str) -> int:
        return self._read("MATCH (e:EvidenceEvent {run_id: $run_id, event: 'mutated'}) "
                          "RETURN count(e) AS n", run_id=run_id)[0]["n"]

    def evidence_of(self, run_id: str) -> list[dict]:
        return self._nodes("MATCH (v:Evidence {run_id: $run_id}) RETURN v "
                           "ORDER BY v.created_at, v.evidence_id", "Evidence", run_id=run_id)

    def evidence_events_of(self, run_id: str) -> list[dict]:
        return self._nodes("MATCH (e:EvidenceEvent {run_id: $run_id}) RETURN e ORDER BY e.seq",
                           "EvidenceEvent", run_id=run_id)

    def spans_of(self, run_id: str, kind: str | None = None) -> list[dict]:
        if kind is None:
            return self._nodes("MATCH (n:Span {run_id: $run_id}) RETURN n ORDER BY n.started_at",
                               "Span", run_id=run_id)
        return self._nodes("MATCH (n:Span {run_id: $run_id, kind: $kind}) RETURN n "
                           "ORDER BY n.started_at", "Span", run_id=run_id, kind=kind)

    def has_open_model_span(self, agent_id: str) -> bool:
        return bool(self._read("MATCH (n:Span {agent_id: $a, kind: 'model', status: 'open'}) "
                               "RETURN n LIMIT 1", a=agent_id))

    def count_model_spans(self, run_id: str) -> int:
        return self._read("MATCH (n:Span {run_id: $run_id, kind: 'model'}) RETURN count(n) AS n",
                          run_id=run_id)[0]["n"]

    # -- labels, pointers --------------------------------------------------------------

    def replace_labels(self, run_id: str, rows: list[dict]) -> None:
        def work(tx):
            tx.run("MATCH (l:Label {run_id: $run_id}) DELETE l", run_id=run_id)
            for r in rows:
                needed = r.get("needed_calls")
                if isinstance(needed, str):
                    needed = json.loads(needed)
                flag = lambda key: None if r.get(key) is None else bool(r[key])  # noqa: E731
                tx.run("CREATE (l:Label {key: $key, run_id: $run_id, kind: $kind, "
                       "subject_id: $subject_id, agent_id: $agent_id, tool_name: $tool_name, "
                       "h_actual: $h_actual, arrived_call: $arrived_call, "
                       "needed_calls: $needed_calls, refetched: $refetched, "
                       "referenced: $referenced, reacquired_same_version: $same, "
                       "reacquired_after_mutation: $after})",
                       key=f"{run_id}{SEP}{r['kind']}{SEP}{r['subject_id']}", run_id=run_id,
                       kind=r["kind"], subject_id=r["subject_id"], agent_id=r.get("agent_id"),
                       tool_name=r.get("tool_name"), h_actual=r.get("h_actual"),
                       arrived_call=r.get("arrived_call"), needed_calls=_j(needed),
                       refetched=flag("refetched"), referenced=flag("referenced"),
                       same=flag("reacquired_same_version"),
                       after=flag("reacquired_after_mutation"))
        self._tx(work)

    def result_labels(self, run_id: str) -> list[dict]:
        return [_decode(r) for r in self._read(
            "MATCH (l:Label {run_id: $run_id, kind: 'result'}) RETURN l.agent_id AS agent_id, "
            "l.tool_name AS tool_name, l.arrived_call AS arrived_call, "
            "l.needed_calls AS needed_calls, l.refetched AS refetched, "
            "l.referenced AS referenced, l.subject_id AS subject_id ORDER BY l.subject_id",
            run_id=run_id)]

    def labels_of(self, run_id: str, kind: str | None = None) -> list[dict]:
        if kind is None:
            return self._nodes("MATCH (l:Label {run_id: $run_id}) RETURN l ORDER BY l.kind, "
                               "l.subject_id", "Label", run_id=run_id)
        return self._nodes("MATCH (l:Label {run_id: $run_id, kind: $kind}) RETURN l "
                           "ORDER BY l.subject_id", "Label", run_id=run_id, kind=kind)

    def add_gateway_pointer(self, run_id: str, agent_id: str, tool_call_id: str, text: str) -> None:
        self._write("MERGE (p:GatewayPointer {key: $key}) ON CREATE SET p.run_id = $run_id, "
                    "p.agent_id = $agent_id, p.tool_call_id = $call SET p.text = $text",
                    key=f"{run_id}{SEP}{agent_id}{SEP}{tool_call_id}", run_id=run_id,
                    agent_id=agent_id, call=tool_call_id, text=text)

    def gateway_pointers(self, run_id: str, agent_id: str) -> dict[str, str]:
        return {r["call"]: r["text"] for r in self._read(
            "MATCH (p:GatewayPointer {run_id: $run_id, agent_id: $agent_id}) "
            "RETURN p.tool_call_id AS call, p.text AS text", run_id=run_id, agent_id=agent_id)}

    # -- maintenance ---------------------------------------------------------------------

    def wipe(self) -> None:
        """Delete every node and relationship. For tests and local resets only."""
        self._write("MATCH (n) DETACH DELETE n")


def row_to_segment(r: dict) -> Segment:
    return Segment(
        id=r["segment_id"], run_id=r["run_id"], agent_id=r["agent_id"], native_id=r["native_id"],
        kind=SegmentKind(r["kind"]), text=r["text"], tokens=r["tokens"],
        content_hash=r["content_hash"], source=r.get("source"), version=r.get("version"),
        pinned=bool(r.get("pinned")), needs_exact_bytes=bool(r.get("needs_exact_bytes")),
        pair_id=r.get("pair_id"), role=r["role"],
        zone=Zone(r["zone"]) if r.get("zone") else Zone.WARM,
        representation=Representation(r["representation"]) if r.get("representation")
        else Representation.FULL,
        position=r.get("position", 0) or 0,
        in_window=bool(r.get("in_window", True)))
