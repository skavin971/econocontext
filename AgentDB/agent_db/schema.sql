-- The agent DB on Postgres, trimmed to one agent's context (CHANGES.md): the run and its task, every
-- message as a segment, the window (each segment's position and the form it is shown in, with the
-- exact token count of that form), tool results with what they read, and the workspace version.
-- Invariants: segments and blobs are write-once; times are timestamptz; JSON columns are jsonb;
-- ids are text chosen by the application. Nothing here is a cost: token counts only.

CREATE TABLE IF NOT EXISTS runs (
  run_id              text PRIMARY KEY,
  task                text NOT NULL
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
  native_id           text NOT NULL,
  kind                text NOT NULL,
  text                text NOT NULL,
  tokens              integer NOT NULL,             -- exact, by the served model's tokenizer
  content_hash        text NOT NULL,
  source              text,
  version             text,
  pinned              boolean NOT NULL DEFAULT false,
  needs_exact_bytes   boolean NOT NULL DEFAULT false,
  pair_id             text,                         -- the tool call a segment belongs to
  role                text NOT NULL,
  created_at          timestamptz NOT NULL,
  blob_key            text REFERENCES blobs(blob_key)
);
CREATE INDEX IF NOT EXISTS segments_source ON segments (run_id, source);
CREATE INDEX IF NOT EXISTS segments_pair ON segments (run_id, pair_id);

-- The window: one row per segment shown. position is the message's place in the conversation;
-- part orders the segments of one message (0: the message; 1..: its tool calls, or tool results
-- quoted word for word inside it). form is how the segment is shown; tokens is that form's size.
CREATE TABLE IF NOT EXISTS window_entries (
  run_id              text NOT NULL REFERENCES runs(run_id),
  segment_id          text NOT NULL REFERENCES segments(segment_id),
  position            integer NOT NULL,
  part                integer NOT NULL DEFAULT 0,
  in_window           boolean NOT NULL,
  form                text NOT NULL CHECK (form IN ('FULL', 'SLICE', 'PREVIEW', 'NOTE', 'EVICTED', 'SUMMARY')),
  tokens              integer NOT NULL,
  updated_at          timestamptz NOT NULL,
  PRIMARY KEY (run_id, segment_id)
);
CREATE INDEX IF NOT EXISTS window_order ON window_entries (run_id, in_window, position, part);

CREATE TABLE IF NOT EXISTS source_versions (
  run_id              text NOT NULL REFERENCES runs(run_id),
  source              text NOT NULL,
  version             text NOT NULL,
  updated_at          timestamptz NOT NULL,
  PRIMARY KEY (run_id, source)
);

CREATE TABLE IF NOT EXISTS tool_results (
  run_id              text NOT NULL REFERENCES runs(run_id),
  tool_result_id      text NOT NULL,
  seq                 bigserial,                    -- insertion order: "latest" is the highest
  tool_name           text NOT NULL,
  args_key            text NOT NULL,
  args                jsonb NOT NULL,
  result_segment_id   text NOT NULL REFERENCES segments(segment_id),
  read_set            jsonb NOT NULL,
  side_effect         boolean NOT NULL,
  valid               boolean NOT NULL DEFAULT true,
  created_at          timestamptz NOT NULL,
  PRIMARY KEY (run_id, tool_result_id)
);
CREATE INDEX IF NOT EXISTS tool_results_key ON tool_results (run_id, tool_name, args_key, valid);
