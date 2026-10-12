# Changes in this copy

This copy started from the upstream version of 2026-10-12, committed unchanged (`44e64a0`). Each line below is one change made since then, with its reason. EconoContext uses the store for one agent's context only (step 12a): the run and its task, every message as a segment, the window with each segment's form and exact token count, tool results with what they read, and the workspace version.

## Removed
- `agent_db/neo4j_store.py`: the Neo4j store. Only Postgres is used here.
- `agent_db/memory.py`: the cross-session memory policy. Each trial here keeps its context in its own run, and no session reads another's.
- `integration/test_tier_a_policy.py`: the integration test of that memory policy, removed with it.
- `experiments/` (`bump_race.py`, `recall_check.py`, `replay_sessions.py`): measurements of the memory policy and of concurrent bumps on the upstream setup. Neither is run here; the atomic bump they motivated is kept.
- `upstream_runs/`: the backend shim and test results for the earlier SQLite engine, which this repository no longer has.
- `tests/test_contract.py`: the contract tests, which compare against the earlier SQLite reference store (no longer in this repository) and cover the removed tables. Replaced by `tests/test_store.py`.
- `tests/test_memory.py`: the memory policy's tests, removed with it.
- `schema.sql`, tables `agents`, `stored_results`, `decisions`, `outcomes`, `runtime_spans`, `evidence`, `evidence_events`, `labels` and `gateway_pointers`: bookkeeping outside one agent's context. Decisions go to a log file beside the trial's logs; costs are measured outside the store, which holds no cost.
- `schema.sql`, the GIN full-text index `segments_fts`: nothing searches segment text.
- `schema.sql`, columns of `runs` other than `run_id` (now with `task`): the run's settings are in the trial's own logs.
- `schema.sql`, `agent_id` in `segments`, `window_entries` and `tool_results`: there is one agent per run, so the run id identifies it.
- `postgres.py`, the operations on the removed tables (`end_run`, `set_resolved`, `list_runs`, `runs_of_host`, `labelled_runs`, `upsert_agent`, `add_stored_result`, `find_stored_result`, `snapshot`, decisions, outcomes, spans, evidence, labels and gateway pointers).
- `postgres.py`, `first_segment`, `segments_of`, `assistant_segments`, `search_segments`, `dependents`, `read_paths`, `tool_results_with_text`, `read_sizes`, `count_tool_results`: no caller here.
- `records.py`, `Zone`, `Representation`, `AgentStatus`, `OperatorType`, `Intercept`, `AgentNode`, `ProviderUsage`, `CostBreakdown`, `Candidate`, `Rejection`, `Decision`, `EvidenceRef`, `EvidenceEvent`, `make_ref`: records of the removed tables and operations.
- `records.py`, `count_tokens` (characters / 4, or the caller's estimate when importable): a segment's token count is now always given by the caller, as the served model's exact count.

## Changed
- `schema.sql`, `runs`: `run_id` and `task` only.
- `schema.sql`, `window_entries`: keyed by `(run_id, segment_id)`; `representation` and `zone` replaced by `form`, one of FULL, SLICE, PREVIEW, NOTE, EVICTED or SUMMARY (checked), and `tokens`, the size of the form shown; `part` added, the order of segments within one message (its tool calls, or tool results quoted word for word inside it).
- `schema.sql`, `tool_results`: keyed by `(run_id, tool_result_id)`, so ids are per run; `args` (jsonb) added, the call's arguments, to find earlier reads of a path; `seq` added, the insertion order, so "latest" does not depend on timestamps that can tie.
- `schema.sql`, `segments`: index `segments_pair` on `(run_id, pair_id)`, to join a shown segment to its tool result.
- `postgres.py`, `start_run(run_id, task)`: a run id can be started once only (no `ON CONFLICT`), so two trials never share a window.
- `postgres.py`, `set_window` and `window`: per run, carrying each segment's position, part, form and token count.
- `postgres.py`, `window_results`: new read, the tool results shown in the window with their call, validity, full text and the form shown.
- `postgres.py`, `invalidate`: `tool_results` only (no `stored_results`).
- `postgres.py`, `add_tool_result` and `find_tool_result`: per run, without an agent, with `args`; `find_tool_result` also returns the result's tokens and orders by `seq`.
- `records.py`: `Form` added (the six forms); `Segment` without `agent_id`, `zone` and `representation`, with `part`, `form` and `window_tokens`; `make_segment(run_id, native_id, kind, text, tokens, ...)`, identity `sha256(run_id | native_id | content_hash)`.
- `agent_db/__init__.py`: exports the Postgres store and the records only.
- `tests/conftest.py`: a pgserver Postgres per session, an empty schema and one run per test; the SQLite reference and Neo4j fixtures removed.
- `tests/test_store.py`: new, the kept operations on Postgres, including run isolation and the absence of any cost column.
- `README.md`: rewritten for this copy.
- `pyproject.toml`: the description; the `neo4j` extra and the note on installing EconoContext beside it removed (records are the package's own, and Neo4j is gone).
- `DECISIONS.md`: unchanged; it records the upstream design, including parts removed here.
