-- The Agent DB on Postgres: Part A of AGENT_DB_CONTRACT.md, column for column.
-- Invariants the contract states: NULL means "not reported", never zero; segments and
-- blobs are write-once; only labels rows are deleted (a run's labels replaced whole);
-- times are timestamptz; JSON columns are jsonb; ids are text chosen by the application.

CREATE TABLE IF NOT EXISTS runs (
  run_id              text PRIMARY KEY,
  host                text NOT NULL,
  instance_id         text,
  arm                 text NOT NULL,
  mode                text NOT NULL,
  model               text NOT NULL,
  temperature         double precision NOT NULL,
  config_fingerprint  text NOT NULL,
  started_at          timestamptz NOT NULL,
  ended_at            timestamptz,
  status              text,
  resolved            boolean,
  jev                 boolean NOT NULL DEFAULT false,
  overrides           jsonb,
  workdir             text
);

CREATE TABLE IF NOT EXISTS agents (
  agent_id            text PRIMARY KEY,
  run_id              text NOT NULL REFERENCES runs(run_id),
  parent_id           text REFERENCES agents(agent_id),
  subagent_type       text,
  status              text NOT NULL,
  window_max_tokens   integer,
  created_at          timestamptz NOT NULL,
  updated_at          timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS blobs (
  blob_key            text PRIMARY KEY,
  size                integer NOT NULL,
  data                bytea NOT NULL,
  created_at          timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS segments (
  segment_id          text PRIMARY KEY,
  run_id              text NOT NULL REFERENCES runs(run_id),
  agent_id            text NOT NULL REFERENCES agents(agent_id),
  native_id           text NOT NULL,
  kind                text NOT NULL,
  text                text NOT NULL,
  tokens              integer NOT NULL,
  content_hash        text NOT NULL,
  source              text,
  version             text,
  pinned              boolean NOT NULL DEFAULT false,
  needs_exact_bytes   boolean NOT NULL DEFAULT false,
  pair_id             text,
  role                text NOT NULL,
  created_at          timestamptz NOT NULL,
  blob_key            text REFERENCES blobs(blob_key)
);
CREATE INDEX IF NOT EXISTS segments_source ON segments (run_id, source);
CREATE INDEX IF NOT EXISTS segments_agent_kind ON segments (agent_id, kind, created_at);
-- Full text, per run, ranked (search_segments). A trigram index would serve the
-- substring form better; pg_trgm is not in every build, so this is the portable one.
CREATE INDEX IF NOT EXISTS segments_fts ON segments USING gin (to_tsvector('simple', text));

CREATE TABLE IF NOT EXISTS window_entries (
  agent_id            text NOT NULL REFERENCES agents(agent_id),
  segment_id          text NOT NULL REFERENCES segments(segment_id),
  position            integer NOT NULL,
  in_window           boolean NOT NULL,
  representation      text NOT NULL,
  zone                text NOT NULL,
  updated_at          timestamptz NOT NULL,
  PRIMARY KEY (agent_id, segment_id)
);
CREATE INDEX IF NOT EXISTS window_order ON window_entries (agent_id, in_window, position);

CREATE TABLE IF NOT EXISTS source_versions (
  run_id              text NOT NULL REFERENCES runs(run_id),
  source              text NOT NULL,
  version             text NOT NULL,
  updated_at          timestamptz NOT NULL,
  PRIMARY KEY (run_id, source)
);

CREATE TABLE IF NOT EXISTS tool_results (
  tool_result_id      text PRIMARY KEY,
  run_id              text NOT NULL REFERENCES runs(run_id),
  agent_id            text NOT NULL,
  tool_name           text NOT NULL,
  args_key            text NOT NULL,
  result_segment_id   text NOT NULL REFERENCES segments(segment_id),
  read_set            jsonb NOT NULL,
  side_effect         boolean NOT NULL,
  valid               boolean NOT NULL DEFAULT true,
  created_at          timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS tool_results_key ON tool_results (run_id, tool_name, args_key, valid);

CREATE TABLE IF NOT EXISTS stored_results (
  result_id           text PRIMARY KEY,
  run_id              text NOT NULL REFERENCES runs(run_id),
  task_key            text NOT NULL,
  agent_id            text NOT NULL,
  result_text         text NOT NULL,
  read_set            jsonb NOT NULL,
  side_effect         boolean NOT NULL,
  valid               boolean NOT NULL DEFAULT true,
  created_at          timestamptz NOT NULL,
  blob_key            text REFERENCES blobs(blob_key)
);
CREATE INDEX IF NOT EXISTS stored_results_key ON stored_results (run_id, task_key, valid);

CREATE TABLE IF NOT EXISTS decisions (
  decision_id         text PRIMARY KEY,
  run_id              text NOT NULL REFERENCES runs(run_id),
  agent_id            text NOT NULL,
  intercept           text NOT NULL,
  mode                text NOT NULL,
  candidates          jsonb NOT NULL,
  feasible            jsonb NOT NULL,
  chosen              text NOT NULL,
  applied             boolean NOT NULL,
  predicted_cost      jsonb NOT NULL,
  why_not             jsonb NOT NULL,
  cache_predicted     integer,
  manifest_hash       text,
  decision_ms         double precision NOT NULL,
  error               text,
  created_at          timestamptz NOT NULL,
  prediction          jsonb,
  subject_id          text,
  payloads            jsonb
);
CREATE INDEX IF NOT EXISTS decisions_run ON decisions (run_id, intercept);

CREATE TABLE IF NOT EXISTS outcomes (
  outcome_id          text PRIMARY KEY,
  run_id              text NOT NULL REFERENCES runs(run_id),
  agent_id            text NOT NULL,
  decision_id         text,
  phase               text NOT NULL,
  uncached_input      integer,
  cache_read          integer,
  cache_write         integer,                  -- 5-minute writes (Anthropic) / the only kind
  cache_write_1h      integer,                  -- 1-hour writes (Anthropic), when reported
  output              integer,
  reasoning           integer,
  latency_ms          double precision,
  cost_nu             double precision,
  cost_usd            double precision,
  cost_complete       boolean NOT NULL,
  price_period        text,
  raw                 jsonb NOT NULL,
  created_at          timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS outcomes_run ON outcomes (run_id, agent_id);
CREATE INDEX IF NOT EXISTS outcomes_agent_time ON outcomes (agent_id, created_at);
CREATE INDEX IF NOT EXISTS outcomes_time ON outcomes (created_at);

CREATE TABLE IF NOT EXISTS runtime_spans (
  span_id             text PRIMARY KEY,
  run_id              text NOT NULL REFERENCES runs(run_id),
  agent_id            text NOT NULL,
  kind                text NOT NULL,
  name                text NOT NULL,
  native_id           text,
  decision_id         text,
  started_at          timestamptz NOT NULL,
  ended_at            timestamptz,
  duration_ms         double precision,
  status              text NOT NULL,
  metadata            jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS runtime_spans_run ON runtime_spans (run_id, started_at);
CREATE INDEX IF NOT EXISTS runtime_spans_open ON runtime_spans (run_id, status, agent_id);

-- Evidence: what an agent read, identified by the version it read (econocontext/evidence.py).
-- Write-once. The same path at another version is another piece of evidence.
CREATE TABLE IF NOT EXISTS evidence (
  run_id              text NOT NULL,
  evidence_id         text NOT NULL,            -- sha256(kind | source_key | source_version | range)
  source_kind         text NOT NULL,            -- file | search
  source_key          text NOT NULL,            -- repo-relative path, or '<tool>:<args key>'
  source_version      text NOT NULL,            -- 'sha256:<file bytes>' | 'text:<hash>' | 'epoch:<n>'
  range               text NOT NULL,            -- '' = the whole source
  content_hash        text NOT NULL,            -- sha256 of the text the agent received
  byte_size           integer NOT NULL,
  token_size          integer NOT NULL,
  recoverable         boolean NOT NULL,
  created_at          timestamptz NOT NULL,
  PRIMARY KEY (run_id, evidence_id)
);
CREATE INDEX IF NOT EXISTS evidence_source ON evidence (run_id, source_key);
CREATE INDEX IF NOT EXISTS evidence_version ON evidence (source_version);

-- When evidence entered an agent's context ('acquired', at model call call_no), and when
-- a source changed ('mutated'; source_key '*' = unknown which).
CREATE TABLE IF NOT EXISTS evidence_events (
  run_id              text NOT NULL,
  seq                 integer NOT NULL,         -- order within the run
  agent_id            text NOT NULL,
  call_no             integer NOT NULL,
  event               text NOT NULL,            -- acquired | mutated
  evidence_id         text,                     -- NULL for mutated
  source_key          text NOT NULL,
  tool_name           text NOT NULL,
  args_key            text NOT NULL,
  created_at          timestamptz NOT NULL,
  PRIMARY KEY (run_id, seq),
  FOREIGN KEY (run_id, evidence_id) REFERENCES evidence (run_id, evidence_id)
);
CREATE INDEX IF NOT EXISTS evidence_events_agent ON evidence_events (run_id, agent_id, call_no);

CREATE TABLE IF NOT EXISTS labels (
  run_id              text NOT NULL,
  kind                text NOT NULL,            -- decision | result | evidence
  subject_id          text NOT NULL,
  agent_id            text,
  tool_name           text,
  h_actual            integer,
  arrived_call        integer,
  needed_calls        jsonb,
  refetched           boolean,
  referenced          boolean,
  reacquired_same_version   boolean,            -- evidence: next reacquisition was redundant
  reacquired_after_mutation boolean,            -- evidence: its source changed first
  PRIMARY KEY (run_id, kind, subject_id)
);

-- Databases created before these columns existed get them added.
ALTER TABLE outcomes ADD COLUMN IF NOT EXISTS cache_write_1h integer;
ALTER TABLE labels ADD COLUMN IF NOT EXISTS reacquired_same_version boolean;
ALTER TABLE labels ADD COLUMN IF NOT EXISTS reacquired_after_mutation boolean;

CREATE TABLE IF NOT EXISTS gateway_pointers (
  run_id              text NOT NULL,
  agent_id            text NOT NULL,
  tool_call_id        text NOT NULL,
  text                text NOT NULL,
  PRIMARY KEY (run_id, agent_id, tool_call_id)
);
