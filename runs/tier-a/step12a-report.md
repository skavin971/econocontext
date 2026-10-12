# Step 12a report: the agent DB, part 1 (2026-10-12)

**Branch:** `agentdb-12a`, from `35437d5`. **Commits:**
- `44e64a0`: the upstream drop, unchanged, plus the map;
- `918e30d`: the goldens from the SQLite Owner;
- the build (this report).

**Not pushed.**

## What was done
1. **The upstream `AgentDB/`** was committed unchanged (`44e64a0`). The zip moved to `~/econo/AgentDB-latest.zip`, and `*.zip` is git-ignored.
2. **The goldens were recorded before any change** (`918e30d`), from the SQLite Owner. They are scripted conversations through our harness, with forced rules so that every rule fires.
3. **Clean-up and trim of our copy**, one line per change in `AgentDB/CHANGES.md`, with no names.
   - Removed: `neo4j_store.py`, `memory.py`, `integration/`, `experiments/`, `upstream_runs/`, `tests/test_contract.py` and `tests/test_memory.py`.
   - Kept tables: `runs` (`run_id`, `task`), `segments`, `blobs`, `window_entries`, `tool_results` and `source_versions`.
     - `window_entries` holds `form` (FULL, SLICE, PREVIEW, NOTE, EVICTED or SUMMARY, checked by the database), `tokens` and `part`.
     - `tool_results` gains `args` and `seq`.
   - Dropped: the bookkeeping, agent, stored-result and evidence tables, and the GIN index.
   - New tests of the kept operations: `AgentDB/tests/test_store.py` (10 tests, pgserver).
4. **One shared exact counter:** `measure/tokens.py` `count_text(text, model)`, the measured Qwen tokenizer. `measure/` still never imports `econocontext/`.
5. **The Owner on the agent DB, through named operations only:**
   - writes: `add_segment`, `set_window` (with forms), `add_tool_result` and `bump`;
   - reads: `find_tool_result`, `window`, `window_results` and `current_versions`.

   The rest of the Owner's state:
   - **Run state** is plain variables.
   - **Every message is a segment** at its position, with its tool calls as parts.
   - **Decisions** go to `decisions.jsonl` beside `trajectory.json`.
   - **Jev's input** is built only from window reads plus the task.
   - **`session.py` and `tokens.py` are deleted.** The command rules moved unchanged to `econocontext/commands.py`.
6. **The store is built in `econocontext/policy.py`** from `AGENTDB_DSN` (the environment, else `.env`).
   - Each trial is its own run, `<run_id>:<random>`; a run id can be started only once.
   - Docker: `docker-compose.yml` runs postgres:16 on 127.0.0.1:5433. It is up, and `AGENTDB_DSN` is in `.env`.
7. **Readers:**
   - `run.py` `jev_fallbacks` and `measure/report.py` `run_actions` read `decisions.jsonl`, with a fallback to `session.sqlite3`.
   - On 122 old runs (tc9, gx4, gx23, ta1; 82 Jev questions), `run_actions` gives byte-identical output under the old and new readers.
8. **Isolation tests:**
   - `econocontext/` may import `agent_db` and `measure`;
   - `harness/` and `baselines/` never import `agent_db`;
   - `agent_db` imports none of our packages;
   - no cost, price, usd, FLOP or bill column, field or operation exists in the agent DB;
   - every question to the predictor carries exactly the turns and items the window holds at that moment, and the task stored for the run (`tests/econocontext/test_jev_input.py`).

**Tests:** 143 pass, against 120 at `35437d5` (`runs/tier-a/step12a-6-tests.out`). `pytest -q` now also runs `AgentDB/tests`.

## Where characters ÷ 4 was used
- **`econocontext/owner.py`: 14 call sites**, all now `self.count`, which is `count_text` with the served model:
  - `before_tool`: the serve price and the note price;
  - `after_tool`: the served item's full and shown sizes, the output's size, and the repeat note's size;
  - `_delta_read`: the slice's size, used twice (the window and the price), and the output's size;
  - `_arrival`: each form's size;
  - `_evict`: the messages after the item, and the marker's size;
  - `_compact`: the summary's size, used twice.

  The serve price now uses the stored result's count, which is the same number as before.
- **`AgentDB/agent_db/records.py` `count_tokens`.** It imported `econocontext.tokens` when it could, and otherwise used bytes ÷ 4. It is removed; callers now give the count.
- **`tests/econocontext/test_owner.py`.** Jev's fit was checked with `count_tokens`; it is now checked in Jev's own unit, characters ÷ 2.3.

Not changed:
- **Jev's `CHARS_PER_JEV_TOKEN = 2.3`.** It is Jev's own input limit, in its own model's units, not a Qwen count.
- **The fake upstream in `tests/gateway/test_compaction_log.py`.** It invents `prompt_tokens` with `len(json.dumps) // 4`; it stands in for the provider, not EconoContext.

## The equivalence check (no API calls)
There are six scripted conversations (`tests/econocontext/scenarios.py`), all through our harness with a scripted model and a local shell. Each golden holds every request sent to the model, the decision log without timestamps, and the report.

| scenario | predictor | forced | rules that fire |
|---|---|---|---|
| repeats_and_reads | fixed guesses (prior) | – | 1 (before and after a run), 2, 4 preview, 9 |
| evict_and_serve | prior | 6 | 3, 4, 6, 9 |
| forced_compaction | prior | 4, 7 | 3, 4 forced, 7 forced, 9 |
| predictor_rules | fixed answers | – | 4 slice, 6 by price, 7 by the base rules, 9 |
| kept_and_legacy | prior, legacy interface | 7 | 3 within one reply, 6 none, 7 keeping an item word for word, 1 on the kept copy |
| capped_full | fixed answers (keep) | – | 3 serving an output the harness caps, 6 pricing it |

The first four goldens were committed before the change (`918e30d`). The last two were recorded afterwards from the same SQLite Owner (`918e30d`, in a worktree); re-recording reproduced the first four byte for byte (`step12a-2-goldens.out`).

**The store alone (characters ÷ 4 injected): all 6 identical** (messages, decision log and report). This holds on pgserver (`tests/econocontext/test_equivalence.py`) and on the Docker database read through `.env` (`step12a-8-docker.out`).

Two paths were caught by this check during the build, and both now reproduce the old Owner:
- **A preview's or slice's size.** The old Owner counted only the body of a preview or slice, without its `[EconoContext]` header line.
- **A capped output.** The old Owner sized a FULL output before the harness's 30,000-character cap.

Without the fix, `capped_full` fails.

**With exact Qwen counts (reported, not tested; `step12a-7-equivalence-exact.out`).** Every scenario differs, and only because of the counts.
- **repeats_and_reads: the decisions change.** `data.txt` (100 lines) is 525 tokens at characters ÷ 4 and 1,005 exact, so it crosses the 1,000-token arrival threshold. It becomes a preview, so the repeated `cat` is served rather than noted, and two eviction checks (`6 evict: none`) now happen.
- **The other five: the same decisions** (rule, action, item and call). Only the prices, the compaction note's `summary_tokens`, and the "(about N tokens)" figure in the arrival note shown to the model change. For example, the 401-line file is 7,104 tokens at ÷ 4 and 6,405 exact.

## Findings carried over unchanged (for your decision; nothing was fixed)
1. **The header undercount.** The window records a preview or slice at the size of its body. The one-line header the model also sees is not counted.
2. **The cap.** A FULL output over the 30,000-character cap is recorded at its uncapped size. The window holds the capped text the model sees.
3. **The evict price.** Its "tokens after the item" counts `json.dumps` of each message, so with exact counts it measures JSON-escaped text, not the prompt. Window token sums would be the direct measure.

## Choices made in the build (please check)
- **Two fixed predictors besides prior.** The fixed guesses (prior) never produce a slice, an unforced eviction or a kept item. So two scenarios use fixed answers instead:
  - `FixedPredictor`: needed 0.6, two relevant chunks, done 0.9;
  - `Keeper`: needed 1.0.

  They are still fixed guesses, with no model.
- **Messages with several segments.** `part` in `window_entries` orders the segments of one message: its tool calls, or the tool results a compaction keeps word for word inside message 1. Libin's schema has one entry per segment.
- **`window_tokens` on `Segment`.** The window's size for an entry differs from the segment's own size only in findings 1 and 2.
- **The final messages reach the store.** `report()`, which the harness calls when it saves the trajectory, syncs the window once more.
- **The run id.** It is `<harness run id>:<12 hex>`, so a rerun of the same trial never shares a window.
- **`tool_results` is keyed per run,** and "latest" is decided by `seq`, not by timestamps.

## Logs
- Step logs: `runs/tier-a/step12a-0-status.out` to `step12a-8-docker.out`.
- Scripts: `runs/tier-a/scripts/step12a_record_goldens.py` and `step12a_equivalence.py`.
