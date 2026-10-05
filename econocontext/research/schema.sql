-- A separate, self-contained research database. Never applied to AgentDB.
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blobs (
  blob_key TEXT PRIMARY KEY, data BLOB NOT NULL, size INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, runtime_run_id TEXT NOT NULL,
  started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL DEFAULT 'running',
  capture_status TEXT NOT NULL DEFAULT 'recording', metadata TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_runtime ON runs(runtime_run_id, started_at);
CREATE TABLE IF NOT EXISTS events (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
  run_id TEXT NOT NULL REFERENCES runs(run_id), observed_at TEXT NOT NULL,
  kind TEXT NOT NULL, source TEXT NOT NULL, agent_id TEXT, call_id TEXT,
  decision_id TEXT, parent_event_id TEXT, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run ON events(run_id, sequence);
CREATE INDEX IF NOT EXISTS events_call ON events(run_id, call_id, sequence);
CREATE TABLE IF NOT EXISTS agents (
  run_id TEXT NOT NULL REFERENCES runs(run_id), agent_id TEXT NOT NULL,
  identity_basis TEXT NOT NULL, PRIMARY KEY(run_id, agent_id)
);
CREATE TABLE IF NOT EXISTS model_calls (
  run_id TEXT NOT NULL REFERENCES runs(run_id), call_id TEXT NOT NULL,
  agent_id TEXT, purpose TEXT NOT NULL, model TEXT, provider TEXT,
  started_at TEXT NOT NULL, ended_at TEXT, duration_ms REAL,
  status TEXT NOT NULL DEFAULT 'open', http_status INTEGER,
  decision_id TEXT, received_blob TEXT REFERENCES blobs(blob_key),
  sent_blob TEXT REFERENCES blobs(blob_key), response_blob TEXT REFERENCES blobs(blob_key),
  response_complete INTEGER NOT NULL DEFAULT 0, error TEXT,
  PRIMARY KEY(run_id, call_id)
);
CREATE TABLE IF NOT EXISTS call_messages (
  run_id TEXT NOT NULL, call_id TEXT NOT NULL,
  view TEXT NOT NULL CHECK(view IN ('received','sent','response')),
  position INTEGER NOT NULL, choice_index INTEGER NOT NULL DEFAULT 0,
  role TEXT, tool_call_id TEXT, payload TEXT NOT NULL,
  PRIMARY KEY(run_id, call_id, view, position, choice_index),
  FOREIGN KEY(run_id,call_id) REFERENCES model_calls(run_id,call_id)
);
CREATE TABLE IF NOT EXISTS stream_chunks (
  run_id TEXT NOT NULL, call_id TEXT NOT NULL, chunk_index INTEGER NOT NULL,
  observed_at TEXT NOT NULL, blob_key TEXT NOT NULL REFERENCES blobs(blob_key),
  PRIMARY KEY(run_id,call_id,chunk_index),
  FOREIGN KEY(run_id,call_id) REFERENCES model_calls(run_id,call_id)
);
-- One row per observed phase. Missing native IDs are never paired by FIFO or arguments.
CREATE TABLE IF NOT EXISTS tool_events (
  event_id TEXT PRIMARY KEY REFERENCES events(event_id), run_id TEXT NOT NULL,
  phase TEXT NOT NULL, tool_name TEXT, native_call_id TEXT, native_session_id TEXT,
  attribution TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tool_events_run ON tool_events(run_id, native_call_id);
CREATE TABLE IF NOT EXISTS decisions (
  event_id TEXT PRIMARY KEY REFERENCES events(event_id), run_id TEXT NOT NULL,
  decision_id TEXT NOT NULL, agent_id TEXT, call_id TEXT, parent_event_id TEXT,
  intercept TEXT, chosen TEXT, applied INTEGER NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS decisions_run ON decisions(run_id, decision_id);
CREATE TABLE IF NOT EXISTS usage_costs (
  run_id TEXT NOT NULL, call_id TEXT NOT NULL,
  uncached_input INTEGER, cache_read INTEGER, cache_write INTEGER, output INTEGER,
  reasoning INTEGER, cost_usd REAL, cost_nu REAL, cost_complete INTEGER NOT NULL,
  raw_usage TEXT NOT NULL, pricing TEXT, price_period TEXT,
  PRIMARY KEY(run_id,call_id),
  FOREIGN KEY(run_id,call_id) REFERENCES model_calls(run_id,call_id)
);
CREATE TABLE IF NOT EXISTS artifacts (
  event_id TEXT PRIMARY KEY REFERENCES events(event_id), run_id TEXT NOT NULL,
  name TEXT NOT NULL, media_type TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS capture_issues (
  run_id TEXT NOT NULL REFERENCES runs(run_id), event_id TEXT,
  code TEXT NOT NULL, detail TEXT NOT NULL
);
