"""SQLite observation sink. Writes are atomic, bounded, and fail open.

No operational database imports or queries. Large payloads live in this database's
own blob table. Event data is immutable; convenience projections track call/run state.
"""

import hashlib
import json
import logging
import os
import sqlite3
import threading
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

APPLICATION_ID = 0x45435231  # ECR1; prevents accidentally opening an operational DB
SCHEMA_VERSION = 1
log = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc).isoformat()


def record_failure(path, run_id, kind, exc):
    """Emergency journal outside SQLite; no request data, credentials, or exception text."""
    log.error("Research capture failed for %s (%s): %s", run_id, kind, type(exc).__name__)
    try:
        entry = dict(run_id=run_id, at=now(), kind=kind, error=type(exc).__name__)
        with open(str(path) + ".errors.jsonl", "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        log.error("Could not write research capture failure journal")


def plain(value):
    if is_dataclass(value):
        return plain(asdict(value))
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def check_path(path, operational_path=None):
    path = Path(path).expanduser().resolve()
    if operational_path is not None:
        other = Path(operational_path).resolve()
        if path == other or (path.exists() and other.exists() and os.path.samefile(path, other)):
            raise ValueError("Research and operational databases must be separate files")
    return path


class ResearchRecorder:
    def __init__(self, path, run_id: str, *, operational_path=None):
        self.path = check_path(path, operational_path)
        self.run_id = run_id
        self.failed = False
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=0.1, check_same_thread=False)
        try:
            # Check before setting journal mode or executing any schema statements.
            app = self.conn.execute("PRAGMA application_id").fetchone()[0]
            tables = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if app != APPLICATION_ID and (app != 0 or tables):
                raise ValueError("Refusing to write to a database not owned by the research recorder")
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError("Research database schema is newer than this recorder")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA journal_mode=WAL")
            schema = Path(__file__).with_name("schema.sql").read_text()
            self.conn.executescript("BEGIN IMMEDIATE;\n" + schema +
                                    f"\nPRAGMA application_id={APPLICATION_ID};\n"
                                    f"PRAGMA user_version={SCHEMA_VERSION};\nCOMMIT;")
            with self.conn:
                self.conn.execute("INSERT OR IGNORE INTO schema_migrations VALUES(?,?)",
                                  (SCHEMA_VERSION, now()))
        except Exception:
            self.conn.close()
            raise

    @classmethod
    def start(cls, path, runtime_run_id: str, metadata: dict, *, operational_path=None):
        recorder = cls(path, uuid.uuid4().hex, operational_path=operational_path)
        recorder.emit("run.started", {"runtime_run_id": runtime_run_id, "metadata": metadata},
                      source="bench")
        return recorder

    def close(self):
        with self.lock:
            self.conn.close()

    def _blob(self, data: bytes) -> dict:
        key = hashlib.sha256(data).hexdigest()
        self.conn.execute("INSERT OR IGNORE INTO blobs VALUES(?,?,?)", (key, data, len(data)))
        return {"$blob": key, "size": len(data)}

    def _pack(self, value):
        if isinstance(value, bytes):
            return self._blob(value)
        if isinstance(value, str) and len(value) > 4096:
            return {**self._blob(value.encode()), "encoding": "utf-8"}
        if isinstance(value, dict):
            packed = {k: self._pack(v) for k, v in value.items()}
            # Payloads may themselves contain our reserved marker keys.
            return {"$literal": packed} if "$blob" in value or "$literal" in value else packed
        if isinstance(value, list):
            return [self._pack(v) for v in value]
        return value

    def _json(self, value):
        return json.dumps(self._pack(plain(value)), ensure_ascii=False, allow_nan=False)

    def _failure(self, kind, exc):
        self.failed = True
        record_failure(self.path, self.run_id, kind, exc)

    def emit(self, kind: str, payload, *, source="runtime", agent_id=None, call_id=None,
             decision_id=None, parent_event_id=None, event_id=None):
        """Append a snapshot; never return a value used by the runtime or raise to it."""
        if self.failed:
            return
        try:
            with self.lock, self.conn:
                p = plain(payload)
                eid, at = event_id or uuid.uuid4().hex, now()
                if self.conn.execute("SELECT 1 FROM events WHERE event_id=?", (eid,)).fetchone():
                    return
                if kind == "run.started":
                    self.conn.execute("INSERT INTO runs(run_id,runtime_run_id,started_at,metadata) "
                                      "VALUES(?,?,?,?)", (self.run_id, p["runtime_run_id"], at,
                                                          self._json(p["metadata"])))
                packed = self._json(p)
                self.conn.execute("INSERT INTO events(event_id,run_id,observed_at,kind,source,"
                                  "agent_id,call_id,decision_id,parent_event_id,payload) "
                                  "VALUES(?,?,?,?,?,?,?,?,?,?)", (eid, self.run_id, at, kind, source,
                                  agent_id, call_id, decision_id, parent_event_id, packed))
                self._project(kind, p, eid, at, agent_id, call_id, decision_id, parent_event_id)
        except Exception as exc:
            self._failure(kind, exc)

    def _issue(self, eid, code, detail):
        self.conn.execute("INSERT INTO capture_issues VALUES(?,?,?,?)",
                          (self.run_id, eid, code, detail))

    def _messages(self, cid, view, messages, choice=0):
        for i, m in enumerate(messages):
            self.conn.execute("INSERT INTO call_messages VALUES(?,?,?,?,?,?,?,?)",
                              (self.run_id, cid, view, i, choice, m.get("role"),
                               m.get("tool_call_id"), self._json(m)))

    def _project(self, kind, p, eid, at, aid, cid, did, parent):
        key = (self.run_id, cid)
        if kind == "model.request":
            body = p.get("body") or {}
            basis = p.get("identity_basis", "unknown")
            if aid:
                self.conn.execute("INSERT OR IGNORE INTO agents VALUES(?,?,?)",
                                  (self.run_id, aid, basis))
            if basis == "prompt_hash":
                self._issue(eid, "worker_identity_heuristic", "Worker ID is a hash of its first task")
            self.conn.execute("INSERT INTO model_calls(run_id,call_id,agent_id,purpose,model,provider,"
                              "started_at,received_blob) VALUES(?,?,?,?,?,?,?,?)",
                              (*key, aid, p.get("purpose", "agent"), body.get("model"),
                               p.get("provider"), at, self._blob(p["raw"])["$blob"]))
            self._messages(cid, "received", body.get("messages", []))
        elif kind == "model.sent":
            self.conn.execute("UPDATE model_calls SET sent_blob=?,decision_id=? WHERE run_id=? AND call_id=?",
                              (self._blob(p["raw"])["$blob"], did, *key))
            self._messages(cid, "sent", p.get("body", {}).get("messages", []))
        elif kind == "model.chunk":
            self.conn.execute("INSERT INTO stream_chunks VALUES(?,?,?,?,?)",
                              (*key, p["index"], at, self._blob(p["raw"])["$blob"]))
        elif kind == "model.response":
            self.conn.execute("UPDATE model_calls SET response_blob=? WHERE run_id=? AND call_id=?",
                              (self._blob(p["raw"])["$blob"], *key))
            for i, choice in enumerate((p.get("body") or {}).get("choices", [])):
                if isinstance(choice.get("message"), dict):
                    self._messages(cid, "response", [choice["message"]], choice.get("index", i))
        elif kind == "model.finished":
            self.conn.execute("UPDATE model_calls SET ended_at=?,duration_ms=?,status=?,http_status=?,"
                              "response_complete=?,error=? WHERE run_id=? AND call_id=?",
                              (at, p.get("duration_ms"), p["status"], p.get("http_status"),
                               int(p.get("response_complete", False)), self._json(p.get("error")), *key))
            u = p.get("usage") or {}
            self.conn.execute("INSERT INTO usage_costs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (*key, u.get("uncached_input"), u.get("cache_read"), u.get("cache_write"),
                               u.get("output"), u.get("reasoning"), p.get("cost_usd"), p.get("cost_nu"),
                               int(p.get("cost_complete", False)), self._json(p.get("raw_usage")),
                               self._json(p.get("pricing")), p.get("price_period")))
            if not p.get("response_complete") and p["status"] != "refused":
                self._issue(eid, "incomplete_response", "Response failed or ended before completion")
        elif kind == "decision":
            d = p["decision"]
            self.conn.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?,?)",
                              (eid, self.run_id, d["id"], aid, cid, parent, d["intercept"],
                               d["chosen"]["name"], int(d["applied"]), self._json(p)))
        elif kind == "tool.observed":
            event = p["event"]
            request = event.get("request_data") or {}
            data = event.get("data") or {}
            native = event.get("tool_call_id") or request.get("tool_call_id") or data.get("tool_call_id")
            session = event.get("session_id")
            attribution = "native" if native and session else "unresolved"
            self.conn.execute("INSERT INTO tool_events VALUES(?,?,?,?,?,?,?,?)",
                              (eid, self.run_id, event.get("type", "unknown"), event.get("target"),
                               native, session, attribution, self._json(p)))
            if attribution == "unresolved":
                self._issue(eid, "tool_attribution_unresolved", "Policy event lacks native call/session IDs")
        elif kind == "artifact":
            self.conn.execute("INSERT INTO artifacts VALUES(?,?,?,?,?)",
                              (eid, self.run_id, p["name"], p.get("media_type", "application/json"),
                               self._json(p["content"])))
        elif kind == "capture.issue":
            self._issue(eid, p["code"], p["detail"])
        elif kind == "run.finished":
            issues = self.conn.execute("SELECT COUNT(*) FROM capture_issues WHERE run_id=?",
                                       (self.run_id,)).fetchone()[0]
            pending = self.conn.execute("SELECT COUNT(*) FROM model_calls WHERE run_id=? AND status='open'",
                                        (self.run_id,)).fetchone()[0]
            self.conn.execute("UPDATE runs SET ended_at=?,status=?,capture_status=? WHERE run_id=?",
                              (at, p["status"], "partial" if issues or pending else "complete", self.run_id))
