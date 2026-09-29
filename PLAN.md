> **Update 2026-09-28:** the host moved to Omnigent. `adapters/` and `hosts/` (Deep Agents) were removed (git tag `deepagents-host`); the platform layer is `omnigent_layer/` (gateway + policy) and the experiment is `bench/`. The sections below that describe the Deep Agents adapter and host are history. Current shape: README.md; what we checked: docs/omnigent-findings.md.

> **Update 2026-09-27:** the ledger moved from `monitor/ledger.py` to `pricing/ledger.py` and was cleared to a skeleton for the next person to build. `--jev` and `planner/jev_planner.py` were added. See README.md and docs/TESTING.md.

# PLAN.md — EconoContext, first working version (skeleton + infrastructure + one real integration)

## Approval changes (binding; they override anything below that conflicts)

1. **Measurement in both arms.** The usage callback (`ledger`/`outcomes`) is
   installed in the **baseline arm too**. The arms differ only in the decision
   middleware. `run.py` installs `adapters.deepagents.install_measurement(...)`
   in both arms, and `install_decisions(...)` only in the econo arm.
2. **Byte-identical reuse.** ANSWER_FROM_STORE and REUSE_RESULT return a
   `ToolMessage` whose content is **byte-identical** to the stored tool output.
   **No label in the message.** The reuse is recorded only in the DB
   (`decisions.chosen`, and `applied = 1`).
3. **Optimizer tie-break:** equal NU → lower predicted latency → the host
   default. Tested.
4. **Write barrier after `execute`:** run `git status --porcelain` in `/testbed`
   (through the sandbox) and bump **only the changed paths**. The workspace epoch
   `*` is the fallback, used only if git fails.
5. **Segment identity** is `(agent_id, message_or_tool_call_id, content_hash)`.
   It is written once (`INSERT OR IGNORE`) and never rewritten. `segment_id` is
   derived as `sha256(agent_id | native_id | content_hash)`. Mutable per-window
   state (`position`, `in_window`, `representation`, `zone`) moves to a separate
   `window_entries` table: `(agent_id, segment_id, position, in_window,
   representation, zone, updated_at)`. FTS5 on the external-content table gets
   `AFTER INSERT`, `AFTER UPDATE` and `AFTER DELETE` triggers on `segments`.
6. **New test:** two parallel `task` calls in one turn map to the correct,
   distinct subagent `agent_id`s (the `ContextVar` is set per tool call). The
   test uses LangGraph's parallel tool execution with a recorded plain-data
   message shape, not an LLM.
7. **Fixed temperature in both arms.** `config/econocontext.yaml` gets
   `model.temperature` (value set in Step 2 from the verified Gemini default for
   the model, and recorded), and `runs` gets a `temperature REAL NOT NULL`
   column.
8. **Report.**
   - NU is the primary unit, plus USD at **both** Gemini price periods (promo
     through 2026-12-31, and standard from 2027-01-01).
   - The 5-instance run is **labelled "pipeline check"**, not a comparison of
     effectiveness.
   - `report.py` also prints **observe-mode feasibility counts per operator**:
     the would-be POINTER, RETRIEVE_FROM_STORE, ANSWER_FROM_STORE and REUSE_RESULT
     choices, that is, candidates that passed all gates, whether or not chosen.
   - It prints predicted versus actual cost per decision and per run.

Step 1 of 2. On approval, Step 2 does the following:
- deletes `v1/` entirely
- builds the layout below at the repository root, beside `v0/` (left untouched)
- copies this file to `/PLAN.md`

## Context

EconoContext is now an **interception layer**, not the standalone runner described
in `v0/docs/vision.html`:
- The host harness keeps its loop, tools, state, security and history. It calls
  EconoContext at a few intercept points and carries out the returned decision
  with its own machinery.
- The agent owns logical decisions. EconoContext owns physical ones: window
  contents and order, retrieving instead of recomputing, which worker instance
  runs work, and when edits are committed.
- Fail-open always.

This phase:
- builds every component end to end with a simple base implementation
- integrates **Deep Agents (coding agent)** on **real SWE-bench Verified
  instances** with **Gemini on Vertex AI**
- measures the result exactly

Smarter prediction and cache-aware pricing come next, behind the same interfaces.

---

## 1. Modules (one sentence each) and dependency direction

```
hosts/  ──(knows nothing of EconoContext; only run.py, the composition root, may import adapters)
adapters/ ──imports──► econocontext/ (core)   and the host framework (deepagents, langchain)
econocontext/ ──imports──► stdlib, pyyaml only
```

| Module | One sentence |
|---|---|
| `econocontext/types.py` | Every shared dataclass and enum. The only language components use to talk to each other. |
| `econocontext/config.py` | Loads and validates `config/*.yaml` into typed config objects. No number lives in code. |
| `econocontext/engine.py` | The `EconoContext` facade: the intercept API; routes each call through monitor → planner → optimizer → assembler → guard. |
| `econocontext/host.py` | `Protocol`s a host implements: `PointerStore` (materialize a pointer the agent can reopen), `Executors` (run default, answer from store), `HostCapabilities`. |
| `monitor/registry.py` | The agent tree and each agent's window, in memory, written through to `agents` and `segments`. |
| `monitor/cache_belief.py` | PLACEHOLDER: predicts cached tokens from the last prefix; corrected from reported usage; logged, not yet priced. |
| `monitor/ledger.py` | Exact cost from reported usage × billing rates, in NU and USD, per agent and per run. **Not a placeholder.** |
| `pricing/rates.py` | Turns a price card into ratios relative to that model's uncached input price. |
| `pricing/cost_model.py` | PLACEHOLDER: token-length pricing into the four terms (prepare, work, integrate, leaves_behind), plus latency. |
| `pricing/predictor.py` | PLACEHOLDER: `remaining_turns`, `future_use_score`, `p_need_again`. |
| `planner/candidates.py` | The operator catalog: what each operator is, exact or approximate, implemented or "not generated yet". |
| `planner/planner.py` | Rule-based candidate generation per intercept. Always includes the host default. |
| `optimizer/gates.py` | Feasibility gates, each returning `(ok, reason)`. |
| `optimizer/optimizer.py` | `select(candidates, context, constraints) -> Decision`: gates, then price, then choose by objective. Every `why_not` logged. |
| `assembler/zones.py` | Assigns FROZEN / SLOW / WARM / VOLATILE, respecting message dependencies. |
| `assembler/assembler.py` | Renders the chosen plan into ordered segments plus a content-addressed manifest. **Never chooses.** |
| `guard/fail_open.py` | Wraps every engine call. Returns the host default on error; measures the decision deadline. |
| `guard/validate.py` | Checks pairing, pinned segments and window size before a request is returned. |
| `store/schema.sql`, `store/db.py` | The Agent DB (SQLite, plain SQL): writes, reads, invalidation. |
| `store/retrieval.py` | `search(...)`: keyword retrieval over stored segments (FTS5). |
| `adapters/deepagents/middleware.py` | LangChain `AgentMiddleware` → engine calls. Translation only. |
| `adapters/deepagents/callbacks.py` | LangChain callback handler → `engine.record` for **every** model call, including calls made inside other middleware (see §8.3). |
| `adapters/deepagents/executors.py` | Implements `host.py` for Deep Agents: pointer files through the backend, answering from the store, FRESH through the native `task` tool, the explicit general-purpose subagent spec (see §8.4). |
| `adapters/deepagents/translate.py` | LangChain messages ⇄ `Segment`s. |
| `adapters/providers/gemini_usage.py` | LangChain/Gemini usage metadata → `ProviderUsage`, keeping the raw fields. |
| `adapters/providers/anthropic_usage.py`, `openai_usage.py` | Stubs with documented field mappings, for later hosts. |
| `hosts/swebench_deepagents/agent.py` | A Deep Agents coding agent: Gemini, the default subagent, and a sandbox backend on the instance's official image. No EconoContext import. |
| `hosts/swebench_deepagents/sandbox.py` | A Docker sandbox backend (`BaseSandbox`: `execute`, `upload_files`, `download_files`, `id`) on the SWE-bench instance image (see §8.5). |
| `hosts/swebench_deepagents/tasks.py` | Loads SWE-bench Verified instances (id, image, `problem_statement`, `base_commit`). |
| `hosts/swebench_deepagents/run.py` | The composition root. Runs an instance in arm `baseline` or `econo`; the arms differ only by the adapter install. Writes predictions JSONL and enforces budgets. |
| `hosts/swebench_deepagents/evaluate.py` | Calls the official `swebench` harness (Docker). Stops with a clear message if Docker is missing. |
| `scripts/run_baseline.sh`, `run_econo.sh`, `report.py` | Arm runners (they export `.env` into the environment) and the side-by-side report. |

---

## 2. `econocontext/store/schema.sql` (draft)

```sql
-- The Agent DB. Everything EconoContext knows is written here so the planner can
-- retrieve instead of recompute, and future predictors can learn from history.
-- Plain SQLite. Times are UTC ISO-8601 text. Token counts are integers.
PRAGMA foreign_keys = ON;

-- One row per task run. Separates baseline from EconoContext runs and fingerprints
-- the config, so results are never compared across different settings unnoticed.
CREATE TABLE IF NOT EXISTS runs (
  run_id              TEXT PRIMARY KEY,
  host                TEXT NOT NULL,            -- e.g. 'swebench_deepagents'
  instance_id         TEXT,                     -- SWE-bench instance, when applicable
  arm                 TEXT NOT NULL,            -- 'baseline' | 'econo'
  mode                TEXT NOT NULL,            -- 'observe' | 'autopilot' (econo arm)
  model               TEXT NOT NULL,            -- provider model id
  config_fingerprint  TEXT NOT NULL,            -- sha256 of the resolved config
  started_at          TEXT NOT NULL,
  ended_at            TEXT,
  status              TEXT                      -- 'completed' | 'failed' | 'budget_stopped' | 'step_limit'
);

-- The agent tree: the main agent and every subagent instance.
CREATE TABLE IF NOT EXISTS agents (
  agent_id            TEXT PRIMARY KEY,         -- root: '<run>:root'; subagent: '<run>:task:<tool_call_id>'
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  parent_id           TEXT REFERENCES agents(agent_id),
  subagent_type       TEXT,                     -- NULL for the root
  status              TEXT NOT NULL,            -- 'busy' | 'idle' | 'retired'
  window_max_tokens   INTEGER,
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);

-- Every piece of context any agent saw. Evicted or pointered content stays here and
-- can come back without an LLM call. A window = this agent's rows with in_window = 1,
-- ordered by position.
CREATE TABLE IF NOT EXISTS segments (
  segment_id          TEXT PRIMARY KEY,
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  agent_id            TEXT NOT NULL REFERENCES agents(agent_id),
  kind                TEXT NOT NULL,            -- system|tools|task|message|tool_call|tool_result|subagent_result
  text                TEXT NOT NULL,            -- full text, always (even when the window holds a pointer)
  tokens              INTEGER NOT NULL,         -- estimate via the single token-count function
  zone                TEXT NOT NULL,            -- FROZEN|SLOW|WARM|VOLATILE
  representation      TEXT NOT NULL,            -- FULL|POINTER (COMPRESSED|STRUCTURED reserved)
  source              TEXT,                     -- file path, 'tool:<name>(<args>)', or NULL
  version             TEXT,                     -- source version at capture (content hash or epoch)
  content_hash        TEXT NOT NULL,            -- sha256(text); prefix hashing and dedupe
  pinned              INTEGER NOT NULL DEFAULT 0,
  needs_exact_bytes   INTEGER NOT NULL DEFAULT 0,
  pair_id             TEXT,                     -- tool_call id linking a call to its result
  position            INTEGER NOT NULL,         -- order within the agent's window
  in_window           INTEGER NOT NULL DEFAULT 1,
  created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS segments_window ON segments(agent_id, in_window, position);
CREATE INDEX IF NOT EXISTS segments_source ON segments(run_id, source, version);

-- Keyword index over segment text. FTS5 is available in this Python's SQLite (3.45.3);
-- db.py falls back to LIKE if FTS5 is missing, with a comment.
CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
  text, segment_id UNINDEXED, run_id UNINDEXED, content='segments', content_rowid='rowid'
);

-- Source versions: the write barrier bumps these; reuse checks compare against them.
CREATE TABLE IF NOT EXISTS source_versions (
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  source              TEXT NOT NULL,            -- a path, or '*' for the workspace epoch
  version             TEXT NOT NULL,
  updated_at          TEXT NOT NULL,
  PRIMARY KEY (run_id, source)
);

-- Tool calls and the versions of everything they read. An identical call can be
-- answered from here when nothing it read has changed.
CREATE TABLE IF NOT EXISTS tool_results (
  tool_result_id      TEXT PRIMARY KEY,
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  tool_name           TEXT NOT NULL,
  args_key            TEXT NOT NULL,            -- sha256 of normalized arguments
  result_segment_id   TEXT NOT NULL REFERENCES segments(segment_id),
  read_set            TEXT NOT NULL,            -- JSON {source: version}, incl. workspace epoch
  side_effect         INTEGER NOT NULL,         -- 1 for write/edit/execute: never reused
  valid               INTEGER NOT NULL DEFAULT 1, -- 0 once any read source changes
  created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tool_results_key ON tool_results(run_id, tool_name, args_key, valid);

-- Results of delegated work, for REUSE_RESULT.
CREATE TABLE IF NOT EXISTS stored_results (
  result_id           TEXT PRIMARY KEY,
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  task_key            TEXT NOT NULL,            -- sha256(subagent_type + normalized description)
  agent_id            TEXT NOT NULL REFERENCES agents(agent_id),
  result_text         TEXT NOT NULL,
  read_set            TEXT NOT NULL,            -- JSON {source: version} of what the subagent read
  side_effect         INTEGER NOT NULL,         -- 1 if the subagent wrote or executed anything
  valid               INTEGER NOT NULL DEFAULT 1,
  created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS stored_results_key ON stored_results(run_id, task_key, valid);

-- The EXPLAIN log: every intercept decision, every candidate, why each lost.
CREATE TABLE IF NOT EXISTS decisions (
  decision_id         TEXT PRIMARY KEY,
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  agent_id            TEXT NOT NULL,
  intercept           TEXT NOT NULL,            -- plan_prompt|before_tool_call|admit_tool_result|plan_dispatch
  candidates          TEXT NOT NULL,            -- JSON list of candidates with their predicted CostBreakdown
  chosen              TEXT NOT NULL,            -- operator name
  applied             INTEGER NOT NULL,         -- 0 in observe mode, or on fail-open
  predicted_cost      TEXT NOT NULL,            -- JSON CostBreakdown of the chosen candidate (NU, latency_ms)
  why_not             TEXT NOT NULL,            -- JSON [{name, reason}]
  cache_predicted     INTEGER,                  -- cache_belief prediction before the call (plan_prompt only)
  manifest_hash       TEXT,                     -- assembler manifest (plan_prompt only)
  decision_ms         REAL NOT NULL,            -- time spent deciding
  error               TEXT,                     -- set when guard failed open
  created_at          TEXT NOT NULL
);

-- What each model call actually cost. Predicted vs actual joins decisions to outcomes.
CREATE TABLE IF NOT EXISTS outcomes (
  outcome_id          TEXT PRIMARY KEY,         -- the callback run id: one row per physical call
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  agent_id            TEXT NOT NULL,
  decision_id         TEXT,                     -- the plan_prompt decision for this call, if any
  phase               TEXT NOT NULL,            -- 'agent' | 'compaction' (host summarization)
  uncached_input      INTEGER, cache_read INTEGER, cache_write INTEGER, output INTEGER,
  reasoning           INTEGER,                  -- subset of output, for explanation only
  latency_ms          REAL,
  cost_nu             REAL, cost_usd REAL,      -- from ledger + billing_rates.yaml
  raw                 TEXT NOT NULL,            -- original usage fields, JSON, for audit
  created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS outcomes_run ON outcomes(run_id, agent_id);
```

---

## 3. Dataclasses (`econocontext/types.py`)

- **Enums**
  - `Zone`: FROZEN, SLOW, WARM, VOLATILE
  - `Representation`: FULL, POINTER, COMPRESSED, STRUCTURED (the last two unused)
  - `SegmentKind`: SYSTEM, TOOLS, TASK, MESSAGE, TOOL_CALL, TOOL_RESULT, SUBAGENT_RESULT
  - `AgentStatus`: BUSY, IDLE, RETIRED
  - `OperatorType`: EXACT, APPROXIMATE
  - `Intercept`: PLAN_PROMPT, BEFORE_TOOL_CALL, ADMIT_TOOL_RESULT, PLAN_DISPATCH
  - `Mode`: OBSERVE, AUTOPILOT
  - `Objective`: COST, LATENCY, BALANCED
- **`Segment`:** `id, run_id, agent_id, kind, text, tokens, zone, representation,
  source, version, content_hash, pinned, needs_exact_bytes, pair_id, position,
  in_window`.
- **`AgentNode`:** `agent_id, parent_id, subagent_type, status,
  window_max_tokens, read_set: dict[str, str]`.
- **`CacheState`:** `last_prefix_hashes: list[str], last_sent_at,
  ttl_seconds, observed_hit_ratio`.
- **`ProviderUsage`:** `uncached_input, cache_read, cache_write, output,
  reasoning, latency_ms, raw: dict`. `None` means not reported, never zero.
- **`HostRequest`:** `agent_id, segments: list[Segment], window_max_tokens`.
  The host's request, already translated.
- **`RenderedRequest`:** `segments, zone_bounds, cache_breakpoint_after_segment_id,
  manifest: Manifest, applied: bool`.
- **`Manifest`:** `zone_hashes: dict[Zone, str], segment_ids, versions,
  token_estimate, config_fingerprint, hash`.
- **`ToolCallEvent`:** `agent_id, call_id, tool_name, args, args_key, side_effect`.
- **`ToolResultEvent`:** `agent_id, call_id, tool_name, text, source, read_set,
  needs_exact_bytes`.
- **`DispatchIntent`:** `agent_id, call_id, subagent_type, description, task_key`.
- **`ToolAction`:** `run_tool: bool, stored_text: str | None,
  from_tool_result_id`.
- **`DispatchOutcome`:** `result_text, reused: bool, result_id`.
- **`CostBreakdown`:** `prepare, work, integrate, leaves_behind` (NU) plus
  `latency_ms`, and a `total` property (money only).
- **`Candidate`:** `name, operator_type, quality_risk, payload: dict`.
- **`Decision`:** `id, intercept, chosen: Candidate, cost: CostBreakdown,
  rejected: list[(name, why_not)], applied, created_at`.
- **`Constraints`:** `objective, max_cost_nu, max_latency_ms,
  max_quality_risk, latency_weight`.
- **`PlanContext`:** `run_id, agent: AgentNode, window: list[Segment],
  cache: CacheState, rates: RateRatios, config`. What gates and the cost model
  see.
- **`RateRatios`:** `model, input=1.0, output_ratio, cache_read_ratio,
  cache_write_ratio, usd_per_nu`.

---

## 4. Data flow per intercept

The following holds throughout. The composition root installs **one middleware
instance per agent** (the root, every declared subagent, and the explicit
general-purpose spec). The subagent's `agent_id` reaches its middleware through a
`ContextVar`: the root's `task` wrapper sets it before calling the native handler.

**`plan_prompt`** (every model call)
1. Deep Agents calls `EconoMiddleware.wrap_model_call(request, handler)`.
2. `translate` turns the system message, tools and messages into a `HostRequest`
   of `Segment`s.
3. `guard.fail_open(engine.plan_prompt, …)`.
4. `registry.sync_window` writes new segments through to `segments`/FTS, and
   updates positions and `in_window`.
5. `cache_belief.predict` computes the longest common prefix with this agent's
   last request (by segment hash), within the TTL and at least
   `min_cacheable_tokens`. It is logged only.
6. The planner proposes AS_IS, ZONED, COMMIT_PENDING (only if the belief says the
   prefix is cold and pointers are pending) and RETRIEVE_FROM_STORE (only if
   allowlisted; `retrieval.search` on keywords from the newest message).
7. The optimizer runs the gates (allowlist, pairing, window, fidelity, quality),
   then `cost_model` prices the survivors and it chooses by objective, writing
   `decisions`.
8. The assembler renders the choice: zones, the cache breakpoint and the manifest.
9. `guard.validate` checks it; on failure the host default is used and the error
   recorded.
10. Mode `observe`: return the host request unchanged. Mode `autopilot`: return
    the rendered request.
11. The adapter rebuilds messages via `request.override(messages=…)` and calls
    `handler`.

**`record`** (every physical model call, including the host's summarization calls)
1. The LangChain callback `on_llm_end` fires.
2. `gemini_usage` maps it to `ProviderUsage`, keeping the raw fields.
3. `engine.record(agent_id, decision_id, usage)`.
4. The ledger computes NU and USD and writes `outcomes`.
5. `cache_belief.correct` compares predicted with reported `cache_read` and
   updates `observed_hit_ratio`.

**`before_tool_call`**
1. `wrap_tool_call`, before the handler.
2. A `ToolCallEvent` is built (with `args_key` and `side_effect` from the tool
   name).
3. `engine.before_tool_call`: the planner proposes RUN_TOOL and ANSWER_FROM_STORE
   (a `tool_results` match that is valid, with an unchanged read-set).
4. Gates: version, side effects, allowlist. Then select, and log.
5. Autopilot plus ANSWER_FROM_STORE: the adapter returns a `ToolMessage` of the
   stored text, labelled as answered from the store, and **does not call the
   handler**. Otherwise it calls the handler.

**`admit_tool_result`**
1. `wrap_tool_call`, after the handler.
2. A `ToolResultEvent` is built.
3. `engine.admit_tool_result`: the registry stores the full segment and a
   `tool_results` row with its read-set.
4. The planner proposes KEEP_FULL and, above `pointer_min_tokens`, POINTER.
5. Gates: fidelity (`needs_exact_bytes`), allowlist. Select, and log.
6. Autopilot plus POINTER: `host.PointerStore.materialize(segment)` returns a
   path the agent can open with `read_file`. The adapter writes it through the
   Deep Agents backend and replaces the `ToolMessage` content with a preview plus
   the path.

**`on_file_write`** (the write barrier)
1. After `write_file` or `edit_file` (for that path), or after `execute`
   (workspace-wide: bump `*`, see §8.8).
2. `source_versions` is bumped.
3. Any `tool_results` or `stored_results` whose read-set holds that source
   becomes `valid = 0`.
4. The registry updates the agents' `read_set`.

**`plan_dispatch`** (the `task` tool)
1. `wrap_tool_call` on `task`.
2. `DispatchIntent`, with
   `task_key = sha256(subagent_type + normalized description)`.
3. `engine.plan_dispatch(intent, run_default)`: the planner proposes FRESH and
   REUSE_RESULT (a valid `stored_results` match with no side effect).
4. Gates, select, log.
5. FRESH: `run_default()` (the native handler spawns the subagent). Its result
   and read-set are stored.
6. REUSE_RESULT (autopilot): a `ToolMessage` of the stored result, labelled as
   reused.

**`on_turn_end`**
1. `after_model`: turn counter plus cache-state bookkeeping (see §8.9).
2. `after_agent` then marks the agent idle or retired.

---

## 5. Verified facts (sources opened 2026-09-25 to 27)

**Gemini 3.6 Flash on Vertex AI** (the chosen model). Source:
[Vertex pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing),
re-read 2026-09-27; its canonical URL is now
`cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing`.

| Price per 1M tokens, global endpoint | Through 2026-12-31 (promo) | From 2027-01-01 |
|---|---|---|
| Input (≤200K and >200K) | $0.75 | $1.50 |
| Cached input (≤200K and >200K) | $0.075 | $0.15 |
| Output (response and reasoning) | $3.75 | $7.50 |

- Explicit-cache storage is **$0.000001 per token-hour**.
- Non-global endpoints cost 10% more.

Caching facts, from the
[context cache overview](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/context-cache/context-cache-overview)
read 2026-09-27:
- "All Google Cloud projects have implicit caching enabled by default. Implicit
  caching provides a **90% discount** on cached tokens"
- Gemini 3.6 Flash is listed for both implicit and explicit caching
- "the **`cachedContentTokenCount`** field in your response's metadata indicates
  the number of tokens in the cached part"
- **minimum cacheable is 4,096 tokens for the Gemini 3 family**
- explicit TTL defaults to 60 min
- no storage cost for implicit caching
- **So the discount does apply to 3.6 Flash.** The "only 2.5" note is out of
  date for this model. Our own measurement agrees: a 12.7K prompt got
  `cache_read = 10,213`, and a 2,874-token prompt never cached (below 4,096).
- **Implicit cache lifetime is not published.** Hits are best-effort: in the
  pilot, 13 of 41 calls on a stable prefix had no hit.

**Why this model:**
- accessible with the credentials here (10 of 10 realistic requests admitted on
  2026-09-26)
- an implicit discount verified in the docs
- the cheapest verified Gemini
- Gemini 3.5 Flash was refused most of the time by shared capacity (429s)

**LangChain integration.** `langchain-google-genai==4.4.0` (the latest on PyPI,
2026-09-27): `ChatGoogleGenerativeAI(model="gemini-3.6-flash", vertexai=True,
api_key=os.environ["AGENT_PLATFORM_API_KEY"])`. Verified live on 2026-09-26 with
this environment's Vertex express API key. Usage is in `AIMessage.usage_metadata`:
- `input_tokens` (includes cached)
- `output_tokens` (includes reasoning)
- `input_token_details.cache_read` (Gemini's `cachedContentTokenCount`)
- `output_token_details.reasoning`

The Gemini-native raw fields are not exposed by the integration, so `raw` stores
the LangChain usage dict (see §8.10). Credential variable present:
`AGENT_PLATFORM_API_KEY`, in `.env`, not exported by default. The run scripts
export it; its value is never printed.

**Deep Agents `deepagents==0.7.19`** (the latest on PyPI, 2026-09-27; installed
source read):
- **Hooks** (`langchain==1.4.2` `AgentMiddleware`): `wrap_model_call`,
  `awrap_model_call`, `wrap_tool_call`, `awrap_tool_call`, `before_model`,
  `after_model`, `before_agent`, `after_agent`. `ModelRequest.override(...)` and
  `ToolCallRequest.override(...)` exist.
- **User middleware is spliced in ahead** of the profile, prompt-caching and
  memory tail. **A same-named middleware replaces a built-in in place.**
- **Declared subagents get only their own spec's `middleware`.** Fork subagents
  also inherit the main agent's. **The default general-purpose subagent does
  not inherit new main middleware.**
- **Default main stack:** Filesystem, SubAgent, **Summarization (on by
  default)**, PatchToolCalls, then profile, prompt caching and the rest.
- **Built-in behaviour:**
  - `read_file` pages at 100 lines
  - `ls`, `glob` and `grep` self-truncate
  - other tool results over **20,000 tokens** (4 chars per token) are evicted to
    `/large_tool_results/<id>` with a head-and-tail preview, i.e. a native,
    fixed-threshold POINTER
- **Backends:** `FilesystemBackend`, `LocalShellBackend` (unsandboxed shell on the
  host), `StateBackend`, `StoreBackend`, `CompositeBackend`, and `BaseSandbox`
  (a subclass implements `execute`, `upload_files`, `download_files`, `id`; every
  file tool runs through `execute`).
- **Chosen:** a small `BaseSandbox` subclass on the SWE-bench instance image
  (§8.5).

**`swebench==5.0.2`** (the latest on PyPI, 2026-09-27; source read):
- `swebench eval verified -p preds.jsonl --run-id … -i <ids>` (the old
  `python -m swebench.harness.run_evaluation` still works). Predictions are
  JSONL with `instance_id`, `model_name_or_path`, `model_patch`.
- It pulls prebuilt Linux images from Docker Hub (`swebench/sweb.eval.x86_64.*`,
  **amd64 only**, verified for our instances). On M-series it suggests
  `--namespace ''` to build locally ("arm64 experimental").
- It recommends x86_64, 120 GB free disk, 16 GB RAM and 8 cores.
- It **caches results by `run_id` + `instance_id`**, so every evaluation needs a
  new run id.

**Other price cards (for later hosts)**

| Card | Input | Output | Cache read | Cache write | Other | Source |
|---|---|---|---|---|---|---|
| Claude Sonnet 5 | $2 | $10 | $0.20 | 5m $2.50, 1h $4 | min cache 1,024 | [pricing](https://platform.claude.com/docs/en/about-claude/pricing), [caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), 2026-09-27 |
| Claude Opus 5.5 | $4 | $20 | $0.20 (0.05×) | 5m $5, 1h $8 | min cache 512 | same, 2026-09-27 |
| gpt-6-sol | $2 short / $4 long | $10 / $15 | $0.20 / $0.40 | $2.50 / $5 | — | [OpenAI pricing](https://developers.openai.com/api/docs/pricing), 2026-09-27 |
| gpt-6-astra | $10 short / $20 long | $50 / $75 | $1 / $2 | $12.50 / $25 | — | same |

- **Anthropic usage fields:** `cache_creation_input_tokens`,
  `cache_read_input_tokens`, `input_tokens` (after the last breakpoint only),
  `cache_creation.ephemeral_5m/1h_input_tokens`. Up to 4 breakpoints.
- **OpenAI:** the short/long context threshold **was not stated on the page:
  `null # TODO: verify`**. Min cache 1,024 tokens on GPT-5.6+. Usage fields
  `usage.input_tokens_details.cached_tokens` and `.cache_write_tokens`
  (GPT-5.6+).

**Machine:** M2 (arm64), 16 GB RAM, Docker 28.3.2 with an 8 CPU / 8 GB VM,
~117 GB free, Python 3.12.7 (`requires-python >=3.11`), SQLite 3.45.3 with FTS5.
In v0, x86 SWE images ran correctly under Docker Desktop emulation.

---

## 6. SWE-bench instances (fixed before any run)

- **Dataset:** `SWE-bench/SWE-bench_Verified` (500 instances).
- **Chosen by rule:** difficulty `"<15 min fix"`, small Python repositories, an
  official amd64 image about 1 GB (checked on Docker Hub), at least one
  PASS_TO_PASS regression test, and five different repositories.
- **Development:** `pytest-dev__pytest-5809`. It has a short statement, 1
  FAIL_TO_PASS, 3 PASS_TO_PASS and a 1.02 GB image. It is not in the comparison
  set, to avoid tuning to it.
- **Comparison (5):**

| Instance | F2P | P2P | Image |
|---|---|---|---|
| `psf__requests-2317` | 8 | 133 | 1.02 GB |
| `pallets__flask-5014` | 1 | 59 | 1.15 GB |
| `pylint-dev__pylint-4970` | 1 | 17 | 1.09 GB |
| `pytest-dev__pytest-7432` | 1 | 77 | 1.03 GB |
| `sphinx-doc__sphinx-8721` | 1 | 3 | 1.10 GB |

- **Excluded:** xarray (a 2 GB image, 958 regression tests) and anything with 0
  PASS_TO_PASS.

---

## 7. Config schema and defaults

**`config/econocontext.yaml`**

```yaml
mode: observe                 # first run of any change must be observe (spec §6.4)
objective: cost               # cost | latency | balanced
constraints:
  max_cost_nu: null           # no per-decision cap; the run-level budget guards spend
  max_latency_ms: null        # latency not constrained yet: the latency model is a placeholder
  max_quality_risk: 0.0       # autopilot starts with exact operators only
  latency_weight: null        # required only when objective = balanced (NU per ms)
allowlist:                    # host defaults are always allowed; others opt in
  AS_IS: true
  ZONED: true
  COMMIT_PENDING: false       # approximate (changes what the model sees): enable after observe
  RETRIEVE_FROM_STORE: false  # approximate; off by default (spec)
  RUN_TOOL: true
  ANSWER_FROM_STORE: true     # exact: same bytes, versions checked
  KEEP_FULL: true
  POINTER: false              # approximate: the model must reopen; enable in a later autopilot run
  FRESH: true
  REUSE_RESULT: true          # exact: same task key, unchanged read-set, no side effects
quality_risk:                 # PLACEHOLDER judgement values; exact operators are 0.0 by definition
  POINTER: 0.2
  COMMIT_PENDING: 0.2
  RETRIEVE_FROM_STORE: 0.5
planner:
  pointer_min_tokens: 2000    # PLACEHOLDER knob: a tenth of Deep Agents' native 20K eviction; tune from observe data
  retrieve_limit: 5           # search() default in the spec
predictor:
  remaining_turns_default: 18 # PLACEHOLDER: half the median model calls (36) of the 2026-09-25 pilot's solved Gemini runs
latency:
  base_ms: 1400               # PLACEHOLDER: median of 10 probe calls, Gemini 3.6 Flash, 2026-09-26
  ms_per_output_token: null   # PLACEHOLDER: calibrate from observe-mode outcomes
cache:
  ttl_seconds: 300            # PLACEHOLDER: Google publishes no implicit-cache lifetime; corrected by observed_hit_ratio
guard:
  decision_deadline_ms: 50    # measured, not enforced; v1 adapter decisions took ~0.5 ms
limits:
  window_max_tokens: null     # TODO: verify Gemini 3.6 Flash input limit from the model page in Step 2
  live_run_budget_usd: 15.0   # whole comparison; pilot runs cost $0.15-0.71 each (5 x 2 arms + dev)
  per_instance_budget_usd: 2.0
  per_instance_step_limit: 100  # model calls per instance, both arms
instances:
  dev: [pytest-dev__pytest-5809]
  comparison: [psf__requests-2317, pallets__flask-5014, pylint-dev__pylint-4970,
               pytest-dev__pytest-7432, sphinx-doc__sphinx-8721]
```

**`config/billing_rates.yaml`**
- The Gemini card, with both **price periods** (`valid_until` and `valid_from`)
  and tiers (≤200K and >200K), plus `explicit_cache_storage_per_mtok_hour: 1.0`,
  `min_cacheable_tokens: 4096` and `implicit_cache_discount_applies: true`, each
  with its source URL and date.
- Two Anthropic and two OpenAI cards as in §5.
- The OpenAI tier threshold is `null # TODO: verify`.

---

## 8. What is wrong, ambiguous or over-engineered in the prompt, with fixes

1. **Layout versus the existing repo.** The root holds `v0/` (the previous
   prototype, which should be kept) and `v1/` (to delete). **Fix:** build the new
   layout at the root beside `v0/`, and point the root README at both.
2. **"`hosts/` knows nothing about EconoContext"** conflicts with `run.py`
   installing it. **Fix:** `agent.py`, `sandbox.py`, `tasks.py` and
   `evaluate.py` never import EconoContext. `run.py` is the single composition
   root that may import `adapters.deepagents.install(...)` in the econo arm. A
   test enforces both.
3. **`record` through the model-call wrapper misses calls.** Deep Agents'
   summarization runs its own model call inside middleware, which
   `wrap_model_call` never sees, so the ledger would under-count. **Fix:**
   `record` goes through a LangChain callback handler, which sees every call,
   including subagents'. Calls tagged `lc_source: summarization` get phase
   `compaction`.
4. **"Install middleware on every declared subagent"** misses the default
   general-purpose subagent. **Fix:** the adapter passes an explicit
   `general-purpose` spec that mirrors Deep Agents' default (its prompt, tools,
   the model) and carries our middleware. Deep Agents supports overriding it by
   name, and the baseline arm receives the same explicit spec.
5. **"Clone at `base_commit`, filesystem backend on the repo, run tests"**: on
   this Mac the repository's dependencies don't exist, and `LocalShellBackend`
   runs unsandboxed on the host. **Fix:** the agent works inside the **official
   SWE-bench instance image**, which already contains `/testbed` at `base_commit`
   and the exact test environment used by evaluation, through a ~120-line
   `BaseSandbox` (`docker exec`, amd64 emulation). No clone is needed. The
   patch is a `git diff` in `/testbed`. v0 proved this pattern.
6. **Apple Silicon.** swebench images are amd64 only, and arm64 is labelled
   experimental. **Fix:** pull the published amd64 images and run them under
   Docker Desktop emulation (it worked in v0), with `--max_workers 1`. If
   evaluation fails for architecture reasons, stop and report; don't substitute.
7. **POINTER is classed as exact in spirit, but it changes what the model
   sees.** **Fix:** POINTER, COMMIT_PENDING and RETRIEVE_FROM_STORE are
   **approximate**. The first autopilot run ("exact operators only") therefore
   exercises AS_IS, ZONED, ANSWER_FROM_STORE and REUSE_RESULT. POINTER needs a
   separately approved run.
8. **The write barrier is incomplete.** `execute` (shell) can change any file.
   **Fix:** `execute` bumps the **workspace epoch** (`*`), invalidating every
   read-set, as well as `write_file` and `edit_file` bumping their path. This is
   conservative and correct.
9. **`on_turn_end ← after_agent` is the wrong hook.** `after_agent` fires once
   per agent invocation, not per turn. **Fix:** `on_turn_end ← after_model`;
   `after_agent` marks the agent idle or retired.
10. **Raw Gemini fields.** `langchain-google-genai` returns normalized
    `usage_metadata`, not `cachedContentTokenCount` verbatim. **Fix:** `raw`
    keeps the LangChain dict, and the mapping (`cache_read` ⇐
    `cachedContentTokenCount`) is documented from the library source.
11. **ZONED on a Deep Agents conversation** is usually identical to AS_IS: the
    system prompt and tools come first, the task is the first user message, and
    history is chronological. **Fix:** keep it (it proves the assembler), but
    expect near-zero effect. Report it honestly.
12. **COMMIT_PENDING needs a source of "pending" edits.** In this phase,
    pointers are only made at arrival. **Fix:** COMMIT_PENDING is generated only
    when retroactive pointer edits are queued, which nothing queues yet. It
    stays in the catalog but is effectively dormant, and is marked PLACEHOLDER.
13. **Deep Agents' native eviction** (over 20K tokens) stays active in both arms
    and runs outside our wrapper. **Fix:** it is counted and reported, so
    comparisons are fair.
14. **Two price periods for Gemini.** Runs after 2026-12-31 bill at double the
    rate. **Fix:** the ledger picks the card by call date.
15. **Pinning.** Pin the direct dependencies in `pyproject.toml`, and commit a
    `requirements.lock` from `pip freeze` for the transitive ones (no new tool).
16. **Python 3.11 versus the environment's 3.12.7.** The code targets 3.11+ and
    runs on 3.12.
17. **The old `v1` cost-service handoff** is superseded. The ledger here is the
    exact accounting, and a separate handoff is not part of this phase.

---

## 9. Implementation order (Step 2), and the test that proves each step

| # | Build | Proven by |
|---|---|---|
| 0 | Delete `v1/`; scaffold the layout, `pyproject.toml`, `README.md`, and `PLAN.md` (this file) | `pytest` collects; the isolation test passes on an empty core |
| 1 | `types.py`, `config.py`, both YAMLs (with sources) | `test_config`: loads, validates, fingerprint stable; `test_isolation`: the core imports only stdlib and yaml; `hosts/` never imports the core except `run.py` |
| 2 | `store/` (schema, db, retrieval) | `test_store`: segments round-trip; FTS finds by path and identifier; a write invalidates dependent `tool_results`/`stored_results` |
| 3 | `pricing/rates.py` and `monitor/ledger.py` | `test_ledger`: reproduces the dollar cost of a known Gemini usage record (a real v0 pilot record) to the cent, using the right price period |
| 4 | `monitor/registry.py` and `cache_belief.py` | `test_registry`: the tree and windows are written through; `test_cache_belief`: the prefix prediction, and predicted versus reported logged |
| 5 | `pricing/cost_model.py`, `predictor.py`, `planner/`, `optimizer/` | `test_gates`: POINTER rejected when exact bytes are needed, reuse rejected after a version change, side effects never reused; `test_optimizer`: `max_latency_ms`/`max_quality_risk` respected, `why_not` recorded |
| 6 | `assembler/` and `guard/` | `test_assembler`: zones ordered, pairs never split, identical inputs give a byte-identical manifest; `test_fail_open`: a raising component returns the host default |
| 7 | `engine.py` | `test_engine`: each intercept end to end on plain data; observe mode returns the host default and logs the decision |
| 8 | `adapters/providers/gemini_usage.py` and `translate.py` | Unit tests on **real recorded** usage dicts and message shapes from the 2026-09-26 probes (plain data, no LLM) |
| 9 | `hosts/`: `sandbox.py`, `agent.py`, `tasks.py`, `evaluate.py` | The **gold-patch check**: the official harness passes the dev instance's reference patch and fails an empty patch (no LLM, Docker) |
| 10 | `adapters/deepagents/`: middleware, callbacks, executors, `install()` | **Live, observe mode**, on `pytest-dev__pytest-5809`: every model call has an `outcomes` row with usage; the registry shows the root and any subagents |
| 11 | `run.py`, `scripts/*`, `report.py` | **Live, autopilot, exact operators**, on the dev instance: a valid patch plus a swebench result. Then **the 5-instance comparison** (baseline vs econo), within the $15 cap: `report.py` prints tokens by category, NU/$, predicted vs actual, cache-read share, pass/fail and decisions by operator |

**Done means:**
- unit tests pass and the observe-mode live test passes
- the 5-instance comparison prints its report
- `grep -rn "# PLACEHOLDER:"` lists every placeholder
- the README explains the architecture on one page, with a text diagram
- no savings are claimed

Paid runs happen only at steps 10–11. Each stays within the configured budgets,
and failed runs count toward cost.
