-- The Agent DB. Everything EconoContext knows is written here, so the planner can
-- retrieve instead of recompute and future predictors can learn from history.
-- Plain SQLite, no ORM. Times are UTC ISO-8601 text. Token counts are integers.
-- The data and API contract for other implementations: docs/AGENT_DB_CONTRACT.md.

PRAGMA foreign_keys = ON;

-- One row per task run. Separates baseline from EconoContext runs and fingerprints
-- the config, so results are never compared across different settings unnoticed.
CREATE TABLE IF NOT EXISTS runs (
  run_id              TEXT PRIMARY KEY,
  host                TEXT NOT NULL,            -- e.g. 'omnigent'
  instance_id         TEXT,                     -- SWE-bench instance, when applicable
  arm                 TEXT NOT NULL,            -- 'baseline' | 'econo'
  mode                TEXT NOT NULL,            -- 'observe' | 'autopilot' | 'measure' (baseline)
  model               TEXT NOT NULL,            -- provider model id
  temperature         REAL NOT NULL,            -- fixed and identical in both arms
  config_fingerprint  TEXT NOT NULL,            -- sha256 of both resolved config files
  started_at          TEXT NOT NULL,
  ended_at            TEXT,
  status              TEXT,                     -- done | timeout | error: ... | interrupted | capped
  resolved            INTEGER,                  -- official SWE-bench result: 1 | 0 | NULL (not evaluated)
  jev                 INTEGER NOT NULL DEFAULT 0, -- 1 if run with --jev (planner asked Jev for p_need_again)
  overrides           TEXT,                     -- JSON config overrides for this run (e.g. allowlist)
  workdir             TEXT                      -- the run's workspace on the host, if it has one
);

-- The agent tree: the main agent and every subagent instance.
CREATE TABLE IF NOT EXISTS agents (
  agent_id            TEXT PRIMARY KEY,         -- root '<run>:root'; worker '<run>:worker:<8 hex>'
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  parent_id           TEXT REFERENCES agents(agent_id),
  subagent_type       TEXT,                     -- NULL for the root
  status              TEXT NOT NULL,            -- busy | idle | retired
  window_max_tokens   INTEGER,
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);

-- Every piece of context any agent saw. Identity is (agent_id, native_id,
-- content_hash); a row is written once and never rewritten, so evicted or
-- pointered content is always here and can come back without an LLM call.
CREATE TABLE IF NOT EXISTS segments (
  segment_id          TEXT PRIMARY KEY,         -- sha256(agent_id | native_id | content_hash)
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  agent_id            TEXT NOT NULL REFERENCES agents(agent_id),
  native_id           TEXT NOT NULL,            -- host message id / tool-call id / positional id
  kind                TEXT NOT NULL,            -- system|tools|task|message|tool_call|tool_result|subagent_result
  text                TEXT NOT NULL,            -- full text, always
  tokens              INTEGER NOT NULL,         -- econocontext.tokens.count_tokens estimate
  content_hash        TEXT NOT NULL,            -- sha256(text)
  source              TEXT,                     -- file path or 'tool:<name>'
  version             TEXT,                     -- version of source at capture
  pinned              INTEGER NOT NULL DEFAULT 0,
  needs_exact_bytes   INTEGER NOT NULL DEFAULT 0,
  pair_id             TEXT,                     -- tool-call id(s) linking a call and its result
  role                TEXT NOT NULL,            -- system | user | assistant | tool (message validity)
  created_at          TEXT NOT NULL,
  blob_key            TEXT                      -- blobs.blob_key for large text (the same bytes, stored once)
);
CREATE INDEX IF NOT EXISTS segments_source ON segments(run_id, source);

-- Content-addressed storage for large content (store/blobs.py). blob_key = sha256(data):
-- identical content is stored once, whichever rows refer to it. Never changed or deleted.
CREATE TABLE IF NOT EXISTS blobs (
  blob_key            TEXT PRIMARY KEY,         -- sha256(data) hex
  size                INTEGER NOT NULL,         -- bytes
  data                BLOB NOT NULL,
  created_at          TEXT NOT NULL
);

-- The mutable part: where each segment sits in an agent's window right now.
-- An agent's window = its rows with in_window = 1, ordered by position.
CREATE TABLE IF NOT EXISTS window_entries (
  agent_id            TEXT NOT NULL REFERENCES agents(agent_id),
  segment_id          TEXT NOT NULL REFERENCES segments(segment_id),
  position            INTEGER NOT NULL,
  in_window           INTEGER NOT NULL,         -- 1 = present in the latest request
  representation      TEXT NOT NULL,            -- FULL | POINTER (COMPRESSED | STRUCTURED reserved)
  zone                TEXT NOT NULL,            -- FROZEN | SLOW | WARM | VOLATILE
  updated_at          TEXT NOT NULL,
  PRIMARY KEY (agent_id, segment_id)
);
CREATE INDEX IF NOT EXISTS window_order ON window_entries(agent_id, in_window, position);

-- Keyword index over segment text (external-content FTS5). The triggers keep it in
-- step with `segments` (segments are write-once, but the triggers make that a
-- property of the schema rather than a promise of the code).
-- FTS BEGIN (skipped when this SQLite lacks FTS5; retrieval then falls back to LIKE)
CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
  text, content='segments', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS segments_ai AFTER INSERT ON segments BEGIN
  INSERT INTO segments_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS segments_ad AFTER DELETE ON segments BEGIN
  INSERT INTO segments_fts(segments_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS segments_au AFTER UPDATE ON segments BEGIN
  INSERT INTO segments_fts(segments_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
  INSERT INTO segments_fts(rowid, text) VALUES (new.rowid, new.text);
END;
-- FTS END

-- Source versions. The write barrier bumps these; reuse checks compare against them.
-- A file path is bumped when that file changes; '*' is the workspace epoch, bumped on
-- every change (search results depend on it) and as the fallback when changed paths
-- cannot be determined.
CREATE TABLE IF NOT EXISTS source_versions (
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  source              TEXT NOT NULL,
  version             TEXT NOT NULL,
  updated_at          TEXT NOT NULL,
  PRIMARY KEY (run_id, source)
);

-- Tool calls and the versions of everything they read. An identical call can be
-- answered from here, byte for byte, when nothing it read has changed.
CREATE TABLE IF NOT EXISTS tool_results (
  tool_result_id      TEXT PRIMARY KEY,
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  agent_id            TEXT NOT NULL,
  tool_name           TEXT NOT NULL,
  args_key            TEXT NOT NULL,            -- sha256 of normalized arguments
  result_segment_id   TEXT NOT NULL REFERENCES segments(segment_id),
  read_set            TEXT NOT NULL,            -- JSON {source: version}
  side_effect         INTEGER NOT NULL,         -- 1 for write/edit/execute: never reused
  valid               INTEGER NOT NULL DEFAULT 1,
  created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tool_results_key ON tool_results(run_id, tool_name, args_key, valid);

-- Results of delegated work, for REUSE_RESULT.
CREATE TABLE IF NOT EXISTS stored_results (
  result_id           TEXT PRIMARY KEY,
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  task_key            TEXT NOT NULL,            -- sha256(subagent_type + normalized description)
  agent_id            TEXT NOT NULL,            -- the subagent that produced it
  result_text         TEXT NOT NULL,            -- byte-identical tool output delivered to the parent
  read_set            TEXT NOT NULL,            -- JSON {source: version} of what the subagent read
  side_effect         INTEGER NOT NULL,         -- 1 if the subagent wrote or executed anything
  valid               INTEGER NOT NULL DEFAULT 1,
  created_at          TEXT NOT NULL,
  blob_key            TEXT                      -- blobs.blob_key when result_text is large
);
CREATE INDEX IF NOT EXISTS stored_results_key ON stored_results(run_id, task_key, valid);

-- The EXPLAIN log: every decision, every candidate and its predicted cost, why each lost.
CREATE TABLE IF NOT EXISTS decisions (
  decision_id         TEXT PRIMARY KEY,
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  agent_id            TEXT NOT NULL,
  intercept           TEXT NOT NULL,            -- plan_prompt|before_tool_call|admit_tool_result|plan_dispatch
  mode                TEXT NOT NULL,            -- observe | autopilot
  candidates          TEXT NOT NULL,            -- JSON {name: CostBreakdown}
  feasible            TEXT NOT NULL,            -- JSON [names that passed every gate]
  chosen              TEXT NOT NULL,            -- operator name
  applied             INTEGER NOT NULL,         -- 1 only when the host actually carried it out
  predicted_cost      TEXT NOT NULL,            -- JSON CostBreakdown of the chosen candidate
  why_not             TEXT NOT NULL,            -- JSON [{name, why_not}]
  cache_predicted     INTEGER,                  -- cache belief before the call (plan_prompt only)
  manifest_hash       TEXT,                     -- assembler manifest (plan_prompt only)
  decision_ms         REAL NOT NULL,            -- time spent deciding
  error               TEXT,                     -- set when the guard failed open
  created_at          TEXT NOT NULL,
  prediction          TEXT,                     -- JSON {p_need_again, source, prior} (admit_tool_result only)
  subject_id          TEXT,                     -- what the decision was about (segment id, args key, ...)
  payloads            TEXT                      -- JSON {candidate: its sizes}, so the decision can be replayed
);
CREATE INDEX IF NOT EXISTS decisions_run ON decisions(run_id, intercept);

-- What each physical model call actually cost. Joined to decisions for predicted vs actual.
CREATE TABLE IF NOT EXISTS outcomes (
  outcome_id          TEXT PRIMARY KEY,         -- the host's id for this call: one row per call
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  agent_id            TEXT NOT NULL,
  decision_id         TEXT,                     -- the plan_prompt decision for this call, if any
  phase               TEXT NOT NULL,            -- 'agent' | 'compaction' (host summarization)
  uncached_input      INTEGER,                  -- NULL = not reported (never read as zero)
  cache_read          INTEGER,
  cache_write         INTEGER,
  output              INTEGER,                  -- includes reasoning
  reasoning           INTEGER,                  -- subset of output, for explanation only
  latency_ms          REAL,
  cost_nu             REAL,
  cost_usd            REAL,
  cost_complete       INTEGER NOT NULL,         -- 0 when some billed counter was not reported
  price_period        TEXT,                     -- which price period valued it
  raw                 TEXT NOT NULL,            -- original usage fields, JSON, for audit
  created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS outcomes_run ON outcomes(run_id, agent_id);

-- Lifecycle timing for model, tool and dispatch work. An open row represents
-- currently running work; completed outcomes remain the cost source of truth.
CREATE TABLE IF NOT EXISTS runtime_spans (
  span_id             TEXT PRIMARY KEY,
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  agent_id            TEXT NOT NULL,
  kind                TEXT NOT NULL,            -- model | tool | dispatch
  name                TEXT NOT NULL,
  native_id           TEXT,
  decision_id         TEXT,
  started_at          TEXT NOT NULL,
  ended_at            TEXT,
  duration_ms         REAL,
  status              TEXT NOT NULL,            -- open | completed | failed
  metadata            TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS runtime_spans_run ON runtime_spans(run_id, started_at);
CREATE INDEX IF NOT EXISTS runtime_spans_open ON runtime_spans(run_id, status, agent_id);

-- Evidence (econocontext/evidence.py): what an agent read, identified by the version it
-- read. Write-once. The same path at another version is another piece of evidence.
CREATE TABLE IF NOT EXISTS evidence (
  run_id              TEXT NOT NULL,
  evidence_id         TEXT NOT NULL,            -- sha256(kind | source_key | source_version | range)
  source_kind         TEXT NOT NULL,            -- file | search
  source_key          TEXT NOT NULL,            -- repo-relative path, or '<tool>:<args key>'
  source_version      TEXT NOT NULL,            -- 'sha256:<file bytes>' | content hash | 'epoch:<n>'
  range               TEXT NOT NULL,            -- '' = whole source
  content_hash        TEXT NOT NULL,            -- sha256 of the text received
  byte_size           INTEGER NOT NULL,
  token_size          INTEGER NOT NULL,
  recoverable         INTEGER NOT NULL,         -- 1 if the source can give these bytes again
  created_at          TEXT NOT NULL,
  PRIMARY KEY (run_id, evidence_id)
);

-- When evidence entered an agent's context ('acquired', at model call call_no), and
-- when a source changed ('mutated'; source_key '*' = unknown which).
CREATE TABLE IF NOT EXISTS evidence_events (
  run_id              TEXT NOT NULL,
  agent_id            TEXT NOT NULL,
  call_no             INTEGER NOT NULL,         -- the model call whose request carried it
  seq                 INTEGER NOT NULL,         -- order within the run
  event               TEXT NOT NULL,            -- acquired | mutated
  evidence_id         TEXT,                     -- NULL for mutated
  source_key          TEXT NOT NULL,
  tool_name           TEXT NOT NULL,
  args_key            TEXT NOT NULL,
  created_at          TEXT NOT NULL,
  PRIMARY KEY (run_id, seq)
);

-- What actually happened, derived after a run (econocontext/learn/labels.py). The truth
-- that replay scores against and that the learned predictors are fitted to.
CREATE TABLE IF NOT EXISTS labels (
  run_id              TEXT NOT NULL,
  kind                TEXT NOT NULL,            -- 'decision' | 'result' | 'evidence'
  subject_id          TEXT NOT NULL,            -- decision_id, or a tool result's segment id
  agent_id            TEXT,
  tool_name           TEXT,
  h_actual            INTEGER,                  -- decision: model calls this agent made afterwards
  arrived_call        INTEGER,                  -- result: the agent's call count when it arrived
  needed_calls        TEXT,                     -- result: JSON call counts at which it was needed again
  refetched           INTEGER,                  -- result: 1 if a later call re-fetched the same thing
  referenced          INTEGER,                  -- result: 1 if later output quoted one of its lines
  reacquired_same_version   INTEGER,            -- evidence: next reacquisition was redundant (same version)
  reacquired_after_mutation INTEGER,            -- evidence: its source changed before the next reacquisition
  PRIMARY KEY (run_id, kind, subject_id)
);
