# Agent DB, part 1 (step 12a): the map

**Branch:** `agentdb-12a`, from `35437d5`. That commit is `47ccfdc` (the frozen interface) plus only the step-11 decisions; `harness/` is identical. `agentdb-v2` is obsolete and left alone.

**Nothing is moved yet.** Each line below is one change, with its reason.

## 1. AgentDB/ becomes Libin's latest
- **The new code:** the working tree's `AgentDB/` already matches `AgentDB.zip` (in the repo root, untracked, made 2026-10-12 14:45) file for file. The zip is not at `~/econo/AgentDB-latest.zip`. The first commit takes the working tree as Libin's latest, unchanged; every later change to it goes in `AgentDB/CHANGES.md` (for a PR to his repo).
- **`AgentDB.zip`:** not committed. It moves out of the repo, to `~/econo/AgentDB-latest.zip`, where you said it would be (see question F).

## 2. Trim the schema to agent context (`AgentDB/agent_db/schema.sql`, `postgres.py`, `records.py`)

**Kept:**

| table | kept as |
|---|---|
| `runs` | `run_id`, `task` only |
| `segments` | Each message of the conversation: the task, assistant turns, tool calls, tool results and summaries. `agent_id` and its foreign key are dropped, since there is no `agents` table. |
| `blobs` | unchanged (large segment texts) |
| `window_entries` | `segment_id`, `position`, `in_window`, `form`, `tokens`. `representation` and `zone` become `form`, one of FULL, SLICE, PREVIEW, NOTE, EVICTED or SUMMARY, with the exact token count of the form shown. `agent_id` is dropped. |
| `tool_results` | as now, plus `args` (jsonb): the Owner looks up earlier reads of a path. `read_set` is `{"*": workspace version}`. |
| `source_versions` | unchanged: the workspace version that `bump` advances |

**Dropped:**
- the tables `outcomes`, `runtime_spans`, `decisions`, `labels`, `gateway_pointers`, `agents`, `stored_results`, `evidence` and `evidence_events`;
- their operations in `postgres.py`;
- the GIN full-text index, and `search_segments` with it.

**`records.py`:** the forms replace `Representation`, and `Zone`, the agent and decision records are dropped.

**Left unchanged and not imported:** `memory.py` (MemoryPolicy) and `neo4j_store.py`, neither used here (see question C).

## 3. The Owner moves onto the agent DB (`econocontext/owner.py`)

The Owner uses only these named operations:
- **`add_segment` / `add_tool_result`:** each tool call and its result, and every other message, as a segment.
- **`set_window` / `set_form`:** the window order, and each entry's form and token count. These replace `update_item`, `drop_from_context` and `in_context`.
- **`find_tool_result(run, tool, args_key)`:** the latest valid, side-effect-free result. This replaces `last_same` with its epoch check, and it is equivalent, because `bump` invalidates every earlier read set.
- **`bump(run, ["*"])`:** a write. This replaces `bump("epoch")`, and `current_versions` replaces `epoch`.
- **Reads:** `window(run)`, which returns the segments in order with their forms and tokens. It feeds `full_copy_in_context`, `reads_of` and the turns Jev sees.

**Other changes in the Owner:**
- **Run state:** `task`, `calls`, `last_check`, `last_compact` and `compactions` become plain variables in the Owner.
- **Decisions:** they are written to `decisions.jsonl` beside the trial's other logs (the agent's logs dir), one JSON line per decision, with the same fields as today.
- **What Jev sees:** Jev's input is the task plus agent-DB reads only (the window's segments in their current forms), not the harness's message list (see question B).

**Removing `session.py`:**
- `tool_key`, `read_only_bash` and `TESTS` move to `econocontext/commands.py`.
- `session.py` is deleted once the Owner's tests pass on Postgres.

## 4. Readers of the old session database
- **`benchmarks/tblite/run.py`:** `jev_fallbacks` reads `decisions.jsonl` instead of `session.sqlite3`.
- **`measure/report.py`:** `run_actions` reads `decisions.jsonl`. `measure/` still never imports `econocontext/`.
- Old runs keep their `session.sqlite3`, and the readers fall back to it when there is no `decisions.jsonl`.

## 5. Postgres
- **Runs:** `docker-compose.yml` (postgres:16) at the repo root, started with `docker compose up -d`. The DSN comes from `AGENTDB_DSN` in `.env`, read by `econocontext/policy.py`.
- **Isolation per trial:** each trial uses its own `run_id`.
- **Tests:** they use `pgserver` (Postgres from pip, no Docker). `.venv` gains `psycopg[binary]` and `pgserver`, through a `pyproject` extra `agentdb`. This is a network install.

## 6. Tests
- **Isolation:**
  - Jev's input is built only from agent-DB reads plus the task (a test feeds a store-backed Owner and checks Jev's state against the store).
  - The agent DB has no cost, price or FLOP field: no column or operation named cost, price, usd, flop or bill. Token counts are allowed.
  - `harness/` and `baselines/` never import `agent_db`.
  - `econocontext/` may import `agent_db` (question A).
- **The Owner's tests:** `tests/econocontext/test_owner.py` and `test_policy.py` run on pgserver.
- **The agent DB's own tests:** they cover the operations we kept, on pgserver. Libin's contract tests compare against the v1 SQLite reference (`econocontext/store/db.py`), which this branch no longer has (it is at tag `econo-jev-final`); see question D.
- **The equivalence check (no API calls):**
  1. Before the switch, record golden files from the current SQLite Owner: scripted conversations through our harness, with the fixed-guess predictor and forced rules so that every rule fires. The goldens hold the decision log (timestamps dropped) and every message sent to the model.
  2. After the switch, the Postgres Owner must reproduce both exactly.

## Questions before I start
- **A. May `econocontext/` import `agent_db`?** The current rule allows only the standard library, yaml and itself. I propose allowing `agent_db` too, with the store built in `econocontext/policy.py` and passed to the Owner.
- **B. Jev's input from the DB means the whole conversation is stored as segments,** not just the tool results, so that Jev's turns come from DB reads. I propose exactly that: every message becomes a segment, with its window position and form.
- **C. Neo4j:** I propose leaving `neo4j_store.py` unchanged and unused, noted in `CHANGES.md` as not trimmed. Trimming it as well would be the alternative.
- **D. Libin's contract tests** need the v1 reference store. I propose replacing them, in our copy, with tests of the trimmed operations on pgserver, noted in `CHANGES.md`.
- **E. `decisions.jsonl` lives in the agent's logs dir,** beside `trajectory.json`. Is that where you want it?
- **F. `AgentDB.zip`:** may I move it to `~/econo/AgentDB-latest.zip`, out of the repo?
