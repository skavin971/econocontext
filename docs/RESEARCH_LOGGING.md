# Research trajectory recording

The optional research recorder saves what happened during an experiment in its own
SQLite file. The agent never reads this database. The operational Agent DB at
`econocontext/data/econocontext.sqlite3` still supplies memory, optimizer state,
learned predictions, and limits exactly as before. This implementation does not
change its schema, storage code, or retention behavior.

The existing runtime setup is described in
[econocontext/docs/TESTING.md](TESTING.md); operational accounting is described in
[econocontext/docs/COST_TRACKING.md](COST_TRACKING.md).

## Running and inspecting an experiment

From the `econocontext/` directory, with the existing Omnigent server, gateway and
Docker setup running:

```sh
uv run --no-sync python bench/run.py --label research1 \
  --instance pytest-dev__pytest-5809 --arm econo --mode observe \
  --research-db data/research.sqlite3
```

The same flag works for `--arm baseline`, `--mode autopilot`, sets, and `--jev`.
Use the interpreter/environment containing the installed Omnigent layer, as for
ordinary benchmark runs. Restart the gateway and Omnigent processes after installing
this change so their imports include the hooks.

Each invocation prints a new research run UUID. Repeating a label/task creates
another research run, even though the existing operational run ID is reused. The
recorder does not reset operational memory; use distinct labels when the experiment
requires distinct operational runs. The worker gateway's existing `/current` route
requires benchmark runs to execute one at a time.

Omit `--research-db` to disable recording. A disabled invocation clears stale
research discovery bindings for its runtime ID/workspace. Baseline research runs
attach the observation policy; it records events and returns no optimizer changes.

All analysis commands read only the research database:

```sh
uv run --no-sync python -m econocontext.research --db data/research.sqlite3 list
uv run --no-sync python -m econocontext.research --db data/research.sqlite3 summary --run UUID
uv run --no-sync python -m econocontext.research --db data/research.sqlite3 export --run UUID \
  --output data/trajectory.jsonl
uv run --no-sync python -m econocontext.research --db data/research.sqlite3 transcript --run UUID \
  --output data/transcript.txt
```

`summary` includes known cost, unpriced calls, capture issues and emergency journal
entries. `export` emits every event in observation order, resolving large payloads;
raw bytes use base64. `transcript` shows each call's complete sent context followed
by its response, so messages repeated in later prompts appear repeatedly. This is
a per-call view, not a deduplicated conversation. Structured content and tool calls
remain JSON. The `list` command displays stored status; use `summary` to account for
cross-process capture failures discovered in the emergency journal.

## What is recorded

| Observation | Contents |
|---|---|
| Run | Research UUID, operational run ID, task/repository/base commit, dataset, full issue, agent YAML, resolved config/fingerprint, price card, code revision/diff/status, package versions, start/end/status |
| Model attempt | Received and actually forwarded request bodies, complete message objects and tool definitions, parameters, model/provider, timestamps, HTTP status, failures and gateway refusals |
| Model response | Full raw body or ordered raw SSE lines, normalized response choices, tool-call argument fragments assembled for analysis, final assistant output even when no subsequent request occurs |
| Usage and cost | Original provider usage, normalized token counters, latency, the call's price card/date period, estimated USD/normalized-unit cost and completeness |
| Optimizer decision | Candidate costs, feasibility, rejection reasons, chosen action, prediction, applied flag, timing/error and links to the observed model call or policy event |
| Tools and policy | Entire delivered tool-call/result event, original structured payloads, native IDs when present, policy return value, file-write notifications |
| Shell | Execution start, command, local execution ID, full stdout/stderr before truncation or path rewriting, exit code, elapsed time, timeout partial output and execution errors |
| Auxiliary predictor | Jev request/response, status, raw usage when provided and timing, separately identified as `purpose=predictor` |
| Benchmark artifacts | Native session ID, Docker image ID when available, final answer, patch, grader stdout/stderr/return code and full evaluation report |

The recorder captures payloads visible to these hooks. It cannot recover private
model reasoning, provider-side attempts/retries, or Omnigent events that were never
delivered. Hidden reasoning token counts can be logged when the provider reports
them. Requests outside this Chat Completions gateway are not captured automatically.
The TypeSafe predictor has its own hook. There is no change to the archived `v0/` code.

HTTP authentication headers and environment variables are not logged. Prompt,
response, tool, artifact and local code-diff contents are preserved without redaction.
The suggested `econocontext/data/` destination is already gitignored.

## Schema and identifiers

The schema lives in
[econocontext/econocontext/research/schema.sql](../econocontext/research/schema.sql).

| Tables | Purpose |
|---|---|
| `runs`, `agents` | Trial metadata and observed agent identities |
| `events` | Append-only observations, with UUID event IDs and a database insertion sequence |
| `model_calls`, `call_messages`, `stream_chunks` | Queryable call lifecycle, received/sent/response message views and exact stream bytes |
| `tool_events`, `decisions` | Queryable policy observations and optimizer choices |
| `usage_costs` | Per-attempt usage and immutable cost/pricing snapshots |
| `artifacts` | Benchmark outcomes and other run products |
| `blobs` | SHA-256-addressed payload bytes stored in this database, independently of operational blobs |
| `capture_issues`, `schema_migrations` | Coverage diagnostics and research schema version |

Every model attempt has a call UUID. Main gateway call IDs also match existing
operational accounting IDs for optional offline comparison. Predictor calls have
their own IDs and do not create operational outcomes. Large strings and raw bytes
use blob references; the export commands materialize them. Event insertion and its
queryable projections are committed together. Reusing an event ID is idempotent;
two distinct occurrences with identical payloads remain two events.

`events.sequence` represents observation/commit order, not a guaranteed causal order
across concurrent processes. `parent_event_id`, `call_id`, and `decision_id` preserve
known links. Shell execution IDs identify local invocations; they are not native
Omnigent tool-call IDs.

Two identity limitations are recorded instead of guessed:

- Omnigent policy events often lack native tool-call/session IDs. Such tool phases
  remain separate observations with `attribution=unresolved`. No pairing by FIFO,
  argument equality or tool name is performed. Native IDs in model messages remain
  available in the full request/response payloads.
- The existing worker routing identifies workers by a hash of the first task.
  Identical tasks can collide. Those agents retain `identity_basis=prompt_hash` and
  produce a capture issue. Policy events are not assigned to a worker without evidence.

A run with these issues is marked `partial` even if all observed payloads were saved.
This prevents a complete-looking transcript from claiming stronger attribution than
the runtime supplies.

## Cost interpretation

The recorder reuses the existing pure price calculation with the call's captured
price card; it does not write to the operational ledger. Pricing applies only when
the requested model matches that card. Missing counters, a different model, or
auxiliary calls without a supported price card leave cost unknown. Jev's raw usage
is retained but its cost is currently unknown. `known_cost_usd` is a subtotal;
`cost_complete=false` means it is not a full run total. These are estimates from
reported usage and configured prices, not provider invoices.

Old records retain their original price snapshot even if configuration changes.
Request/response bodies retain provider fields beyond the normalized columns for
later analysis. In streaming calls, `response_blob` contains a convenience assembly;
`stream_chunks` and `model.chunk` events contain the authoritative wire bytes.

## Failure behavior and implementation boundary

`econocontext/bench/run.py` starts and ends research runs. Small atomic JSON sidecars
in `econocontext/data/research-bindings/` let the gateway, policy, and shell processes
find the sink. These files only select the recording destination. The observer in
`econocontext/econocontext/observation.py` passes snapshots to
`econocontext/econocontext/research/recorder.py`; its results are never used to make
agent decisions. Disabling or failing the recorder does not replace agent results.

An invalid database destination fails before the paid run starts. The recorder
rejects the operational database, its aliases, other non-research SQLite databases,
and research schemas newer than it understands.

The research connection uses WAL, foreign keys and atomic transactions with a
100 ms SQLite lock timeout. Once a sink encounters a write failure, that sink stops
recording and execution continues. A best-effort `<research-db>.errors.jsonl` journal
records the affected run/event kind and exception type; it contains no payloads.
The offline summary reports that capture as partial. If both SQLite and the journal
are unwritable, only stderr can report the failure. A killed process can leave open
calls or a run without an end; summaries mark those partial rather than inventing
completion. No missing usage is filled with zero.

Writes are synchronous and add observation overhead, especially for streaming
responses. The recorder does not promise timing-neutral experiments. Recorded
latencies include instrumentation overhead; no additional model calls are made.
This first version is validated using mocked providers and local HTTP servers;
a live Omnigent/Docker research run remains an integration check before collecting
an experiment dataset.

## Validation

From `econocontext/`:

```sh
PYTHONPATH=omnigent_layer/src uv run --no-sync python -m pytest -q \
  tests/unit/test_research_recorder.py omnigent_layer/tests/test_research_integration.py
PYTHONPATH=omnigent_layer/src uv run --no-sync python -m pytest -q \
  tests omnigent_layer/tests -m 'not live and not docker'
```

The targeted tests cover database separation, repeat trials, concurrent connections,
atomic failures, exports, final responses, streaming interruption, auxiliary calls,
tool ambiguity, untruncated shell output and unchanged gateway responses with
recording enabled, disabled, or failed. They make no paid model calls.
