-- EconoContext run state. Applied by MemoryStore.open() on every start, so a
-- fresh checkout needs no migration step: the store creates itself.
--
-- Two halves. Rows here are small and indexable; every payload is a reference
-- into the content-addressed artifact store on disk (store/artifacts.py), so a
-- row never holds a prompt, a file or a model response, only its digest.
--
-- Owned by the schema workstream. See store/README.md before changing it --
-- the version check below is deliberate and will refuse a mismatched database.

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
