"""Local, run-scoped state. No transaction spans a model or tool call."""

import asyncio
import math
import re
from pathlib import Path

import aiosqlite

from .artifacts import Artifacts
from .contracts import Evidence, Operation, Result, Worker, digest, now, uid
from .state import CandidateMatches, ExecutionState

SCHEMA = """
CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO migrations VALUES(1);
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, request_key TEXT UNIQUE,
 request_hash TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
 kind TEXT NOT NULL, lookup TEXT, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS record_lookup ON records(run_id,kind,lookup);
CREATE TABLE IF NOT EXISTS bindings(run_id TEXT REFERENCES runs(id), source TEXT, evidence_id TEXT
 REFERENCES records(id), PRIMARY KEY(run_id,source));
CREATE TABLE IF NOT EXISTS messages(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE,
 run_id TEXT REFERENCES runs(id), worker_id TEXT REFERENCES records(id), payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS worker_messages ON messages(worker_id,seq);
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE,
 run_id TEXT REFERENCES runs(id), kind TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS run_events ON events(run_id,seq);
CREATE TABLE IF NOT EXISTS search_text(id TEXT PRIMARY KEY REFERENCES records(id),
 run_id TEXT, source TEXT, text TEXT);
"""


class MemoryStore:
    def __init__(self, root: Path):
        self.root = root
        self.artifacts = Artifacts(root / "artifacts")
        self.lock = asyncio.Lock()
        # Set to narrate events as they are written; see `econocontext run --step`.
        self.observer = None

    async def open(self):
        self.db = await aiosqlite.connect(self.root / "state.sqlite3")
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA foreign_keys=ON")
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.executescript(SCHEMA)
        await self.db.commit()
        versions = await self.query("SELECT version FROM migrations ORDER BY version")
        if [r["version"] for r in versions] != [1]:
            await self.db.close()
            raise ValueError("Unsupported database schema version")
        return self

    async def close(self):
        await self.db.close()

    async def query(self, sql, args=()):
        async with self.db.execute(sql, args) as cursor:
            return await cursor.fetchall()

    async def write(self, sql, args=()):
        async with self.lock:
            await self.db.execute(sql, args)
            await self.db.commit()

    async def create_run(self, request, fingerprint):
        request_hash = digest(request.model_dump(mode="json", exclude={"idempotency_key"}))
        async with self.lock:
            if request.idempotency_key:
                rows = await self.query(
                    "SELECT * FROM runs WHERE request_key=?", (request.idempotency_key,)
                )
                if rows:
                    if rows[0]["request_hash"] != request_hash:
                        raise ValueError("Idempotency key was used with a different request")
                    return self.artifacts.read_json(rows[0]["payload"]), False
            run = dict(
                id=uid(),
                status="queued",
                created=now(),
                started=None,
                ended=None,
                request=request.model_dump(mode="json"),
                fingerprint=fingerprint,
                outcome=None,
                verification="unverified",
                reason=None,
            )
            ref = self.artifacts.json(run)
            await self.db.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?)",
                (run["id"], request.idempotency_key, request_hash, "queued", ref),
            )
            await self.db.commit()
            return run, True

    async def run(self, run_id):
        rows = await self.query("SELECT payload FROM runs WHERE id=?", (run_id,))
        if not rows:
            raise KeyError(run_id)
        return self.artifacts.read_json(rows[0]["payload"])

    async def update_run(self, run_id, **changes):
        run = await self.run(run_id)
        run.update(changes)
        await self.write(
            "UPDATE runs SET status=?,payload=? WHERE id=?",
            (run["status"], self.artifacts.json(run), run_id),
        )
        return run

    async def save(self, kind, obj, lookup=""):
        data = obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj
        await self.write(
            "INSERT INTO records VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,lookup=excluded.lookup",
            (data["id"], data["run_id"], kind, lookup, self.artifacts.json(data)),
        )

    async def get(self, record_id, run_id=None):
        rows = await self.query("SELECT * FROM records WHERE id=?", (record_id,))
        if not rows or (run_id and rows[0]["run_id"] != run_id):
            raise KeyError(record_id)
        return self.artifacts.read_json(rows[0]["payload"])

    async def records(self, run_id, kind, lookup=None, limit=100):
        sql = "SELECT payload FROM records WHERE run_id=? AND kind=?"
        args = [run_id, kind]
        if lookup is not None:
            sql += " AND lookup=?"
            args.append(lookup)
        sql += " ORDER BY id LIMIT ?"
        args.append(limit)
        return [self.artifacts.read_json(r["payload"]) for r in await self.query(sql, args)]

    async def observe(self, run_id, source, text, attempt_id=None, kind="text"):
        raw = text.encode()
        ref = self.artifacts.put(raw)
        evidence = Evidence(
            run_id=run_id,
            source=source,
            version=ref,
            payload=ref,
            tokens=math.ceil(len(raw) / 3),
            description=text[:240],
            attempt_id=attempt_id,
            kind=kind,
            excerpt=(0, len(text)),
        )
        await self.save("evidence", evidence, source)
        await self.write(
            "INSERT INTO search_text VALUES(?,?,?,?)", (evidence.id, run_id, source, text)
        )
        await self.write(
            "INSERT INTO bindings VALUES(?,?,?) ON CONFLICT(run_id,source) DO UPDATE SET evidence_id=excluded.evidence_id",
            (run_id, source, evidence.id),
        )
        await self.event(
            run_id,
            "evidence",
            dict(
                source=source,
                evidence_id=evidence.id,
                version=evidence.version,
                bytes=len(raw),
                attempt_id=attempt_id,
            ),
        )
        return evidence

    async def bindings(self, run_id):
        rows = await self.query("SELECT source,evidence_id FROM bindings WHERE run_id=?", (run_id,))
        return {r["source"]: r["evidence_id"] for r in rows}

    async def versions(self, run_id):
        bindings = await self.bindings(run_id)
        return {key: (await self.get(value, run_id))["version"] for key, value in bindings.items()}

    async def compatible(self, run_id, requirements):
        current = await self.versions(run_id)
        return all(current.get(k) == v for k, v in requirements.items())

    async def append(self, worker, message, event_id=None):
        await self.write(
            "INSERT OR IGNORE INTO messages(id,run_id,worker_id,payload) VALUES(?,?,?,?)",
            (event_id or uid(), worker.run_id, worker.id, self.artifacts.json(message)),
        )
        worker.revision += 1
        await self.save("worker", worker, worker.scope)

    async def history(self, worker_id):
        rows = await self.query(
            "SELECT payload FROM messages WHERE worker_id=? ORDER BY seq", (worker_id,)
        )
        return [self.artifacts.read_json(row["payload"]) for row in rows]

    async def event(self, run_id, kind, data, event_id=None):
        payload = dict(data, timestamp=now())
        await self.write(
            "INSERT OR IGNORE INTO events(id,run_id,kind,payload) VALUES(?,?,?,?)",
            (event_id or uid(), run_id, kind, self.artifacts.json(payload)),
        )
        if self.observer:
            self.observer(dict(payload, kind=kind, run_id=run_id))

    async def events(self, run_id, after=0, limit=100):
        rows = await self.query(
            "SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
            (run_id, after, limit),
        )
        return [
            {
                **self.artifacts.read_json(r["payload"]),
                "seq": r["seq"],
                "id": r["id"],
                "kind": r["kind"],
            }
            for r in rows
        ]

    async def all_events(self, run_id):
        result, cursor = [], 0
        while page := await self.events(run_id, cursor, 500):
            result.extend(page)
            cursor = page[-1]["seq"]
        return result

    async def find_candidates(
        self, operation: Operation, state: ExecutionState
    ) -> CandidateMatches:
        run_id = operation.run_id
        words = sorted(
            set(re.findall(r"[a-zA-Z0-9_]{3,}", operation.goal + " " + operation.scope))
        )[:12]
        bindings = await self.bindings(run_id)
        required = list(dict.fromkeys(operation.required))
        for evidence_id in required:
            await self.get(evidence_id, run_id)
        # The index inspects local text; only bounded metadata reaches planning.
        ranking = " + ".join("(s.text LIKE ? OR s.source LIKE ?)" for _ in words) or "0"
        params = [value for word in words for value in (f"%{word}%", f"%{word}%")]
        rows = await self.query(
            f"SELECT s.id, ({ranking}) AS rank FROM search_text s JOIN bindings b ON b.evidence_id=s.id WHERE s.run_id=? ORDER BY rank DESC,s.source,s.id LIMIT 24",
            (*params, run_id),
        )
        direct = [r["id"] for r in rows if r["rank"] > 0][:2]
        related = [r["id"] for r in rows]
        ids = list(dict.fromkeys(required + direct + related))[:32]
        evidence = {key: Evidence(**await self.get(key, run_id)) for key in ids}
        workers = [Worker(**r) for r in await self.records(run_id, "worker")]
        results = [
            Result(**r)
            for r in await self.records(run_id, "result", operation.key(state["fingerprint"]))
        ]
        return dict(
            required=required,
            direct=direct,
            related=related,
            evidence=evidence,
            workers=workers,
            results=results,
            revision=digest(bindings),
        )
