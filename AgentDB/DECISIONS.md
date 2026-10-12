# Agent DB decisions

Decisions about the Agent DB and its contract (`AGENT_DB_CONTRACT.md`, from the
EconoContext repository). The labmate who wrote the contract has given Libin authority
to change it (2026-10-05); every change is recorded here first.

Status is one of **decided** (Libin has decided), **proposed** (in effect in the
implementation or recommended, waiting for Libin's decision), or **open** (under
discussion; no recommendation adopted). Newest last.

---

## D1. Return shapes of the Postgres and Neo4j stores
2026-10-04 · **proposed** (in effect in the implementation)

Timestamps are stored as `timestamptz` and returned as ISO-8601 strings, so they compare
and sort like the reference's text timestamps. JSON columns are `jsonb` and returned
decoded (dict or list), not as JSON text. Booleans are real booleans, not 0/1.

*Contract impact:* Part A says `timestamptz` and `jsonb` but not what an operation
returns. If adopted, Part B gains one line stating these return shapes.

---

## D2. A version bump is atomic under concurrent writers
2026-10-05 · **proposed** (implemented in Postgres; Neo4j already correct)

`bump(run_id, sources)` must advance each version by exactly one even when several
writers bump at once. The gateway process and the host process both write the store,
and parallel workers write from threads. Measured with `experiments/bump_race.py`:
four writers, 50 bumps each, should reach 200.

| store | reached |
|---|---|
| reference SQLite, four connections | 50 |
| reference SQLite, four threads on one store | 70 to 109 |
| Postgres before the fix | 50 |
| Postgres after the fix, and Neo4j | 200 |

A lost bump means a file changed but its version did not move, so a result read in
between still looks valid. The test `test_concurrent_bumps_are_never_lost` holds every
backend to this and is a strict expected failure on the reference.

*Contract impact:* Part B, `bump`: "each source and `*`: version += 1, atomically".
*Follow-up:* the reference's fix is one SQL statement in the labmate's `store/db.py`.

---

## D3. Cross-run reuse: a result is reusable wherever the content matches
2026-10-05 · **decided** (Libin)

A stored result may be reused by any run, of any task, whenever everything it read has
the same content now as when it was produced. Not limited to the same run, the same
task, or the same repository.

*Why:* it is the widest scope that is still exact. A result is a function of what it
read; if those bytes are identical, so is the result.

*What this requires:*
- **Read sets identify content, not per-run counters.** Today a read set maps a path to
  a counter that is "0" until the run first changes that file. "0" means the file as at
  the task's base commit, so it is comparable between runs of the same task, but "2" in
  one run and "2" in another are unrelated. Each read-set entry must carry the content
  hash of what was read. The host's write barrier already computes git blob hashes for
  changed files (`omnigent_layer/workspace.py`) and discards them; unchanged files have
  the blob hash at the base commit.
- **Workspace-wide reads.** A search depends on the whole workspace (`*`). Its content
  identity is the workspace tree hash, so a search result matches only where the whole
  tree is identical. In practice that is the same task at the same state.
- **Lookups across runs**, keyed by tool name and arguments (tool results) or task key
  (delegated results), filtered by matching content hashes.
- **Provenance:** every reuse records the run and task the result came from.

*Contract impact (to specify):* Part A, `tool_results.read_set` and
`stored_results.read_set` values carry `{version, hash}`; a workspace tree hash per run
state. Part B, `find_tool_result` and `find_stored_result` gain a content-matched,
cross-run form that returns provenance. Per-run counters stay, for staleness within a run.

*Amended 2026-10-07 (see D7):* the content identity now exists upstream. The `evidence`
table on the labmate's `econo-jev` branch identifies what an agent read by
`source_version = sha256:<file bytes>` (or `epoch:<n>` for searches, `text:<hash>` when
not recoverable). Cross-run matching should be built on `evidence` and its
`source_version`, not on a hash field of our own in the read sets.

---

## D4. Which kinds of result are shared across runs
2026-10-05 · **open**

Two kinds of stored result differ on payoff, correctness and leakage:

| | tool result | delegated result |
|---|---|---|
| what it is | a tool's output, e.g. the text of a file read | a worker's final answer to a sub-task |
| determined by | the tool, its arguments, and the content read | all that, plus the model's reasoning |
| reuse correct? | yes, if the tool is deterministic | it is one sample: possibly wrong, and reuse gives no second try |
| reveals anything new? | no: the run could read the same bytes itself | yes: another run's conclusions |
| payoff in this host | none in money (the text enters the context either way); little in time (reads are cheap; tests run through a shell, which is classed as a write and never reused) | a whole worker run: all its model and tool calls |

Options: (a) tool results only; (b) both, with D5's rule for delegated results;
(c) both, delegated results only from the same model and prompt configuration.

*Also found:* in the current host nothing stores workers' answers. `plan_dispatch`,
which stores them, is not called by the Omnigent policy, so `stored_results` is empty
in real runs. And a worker that used any shell is classed as having written, so its
answer is never reusable; this excludes "run the tests and report" sub-tasks.

---

## D5. Leakage between runs
2026-10-05 · **open** (recorded for discussion)

*The situation.* In a benchmark, cost savings should come from the optimizer, not from
having seen the task before. If a second run of SWE-bench task T reuses a worker's
answer from the first run of T ("the blank-field bug is in parsing.py line 40"), it is
cheaper because the first run did the work. Reported as optimizer savings, that
overstates them. The labmate's own predictors avoid this kind of leak on purpose: they
learn from other tasks only (`learn/predictors.py`, leave one instance out).

Three cases:
1. **Tool results under content match.** No leak: the reused bytes are what the run
   would get by running the tool itself.
2. **Delegated results from another task with matching content.** Transfer between
   tasks: the research claim of shared memory. Legitimate if declared, but results then
   depend on which runs happened first.
3. **Delegated results from an earlier run of the same task.** A strong leak: prior work
   on the same bug.

*Proposal for discussion:*
- Every reuse records its source run and task (D3 provenance).
- Each run declares, at its start, the pool of runs it may draw from. Fixing the pool
  makes results reproducible regardless of run order, and leave-one-out becomes one
  rule: the pool excludes runs of the same instance.
- Reports split savings by origin: same run, other task, same task.
- Candidate experimental conditions: no cross-run reuse; tool results only; all kinds
  with leave-one-out; all kinds including the same task, reported only as a labelled
  upper bound.

---

## D6. How cross-run reuse is made to pay off
2026-10-05 · **proposed**

1. **Measure before changing behaviour.** In observe mode the engine logs what it would
   do without doing it. Store each worker's answer when its dispatch returns, look up
   the content-matched pool at every dispatch, and log "would have reused result R from
   run X" next to what the worker actually cost (its outcomes). A report sums the
   would-be savings by origin. This shows whether the wiring is worth building, on real
   runs, at no risk.
2. **Then deliver.** A policy cannot skip a tool or a dispatch; it can only replace
   arguments or a result after the fact. The gateway can skip a worker: the worker's
   first model call arrives there, and if a valid stored answer exists, the gateway
   replies itself with that answer as a final message and never calls the model. The
   worker ends at once and the root receives the stored answer. No Omnigent change.
3. **Who builds what.**
   - *Store (Libin):* content-hash read sets, the content-matched cross-run lookup with
     provenance and a per-run pool, and the would-be-savings report, on all three
     backends, with tests.
   - *Host (labmate's code):* store each worker's answer at dispatch end; pass content
     hashes from the write barrier; the gateway short-circuit. These can be prototyped
     in the local worktree for measurement without being pushed.

---

## D7. Adopt the contract additions from the labmate's `econo-jev` branch
2026-10-07 · **proposed** (implemented on both stores)

On 2026-09-30, after the handover, the labmate extended the contract on `econo-jev`
(commit 8c098e5, "add versioned evidence identity"), additively:

- Part A: `evidence` (run_id, evidence_id, source_kind, source_key, source_version, range,
  content_hash, byte_size, token_size, recoverable, created_at), `evidence_events`
  (run_id, seq, agent_id, call_no, event, evidence_id, source_key, tool_name, args_key,
  created_at), `outcomes.cache_write_1h`, `labels.kind` gains `evidence` with
  `reacquired_same_version` and `reacquired_after_mutation`.
- Part B: `add_evidence_events(run_id, agent_id, call_no, events)` (atomic; evidence rows
  once; each event takes the run's next `seq`), `mutations(run_id)`,
  `finish_runtime_span(..., metadata?)` (merged into the span's own).

Both stores now implement all of it; the conformance suite checks each against the
reference on `econo-jev`. `main` does not carry these yet.

*Beyond the contract, as readers only:* `evidence_of(run_id)`,
`evidence_events_of(run_id)`, `labels_of(run_id, kind?)`. The reference exposes none
of these; its callers read the tables with SQL. Proposed for Part B.

*Why:* the contract is shared, and these additions are the content identity D3 needs.
*Follow-up:* the reference's `add_evidence_events` computes `seq` as max+1 under its
in-process lock only; across processes it has the same lost-update shape as D2.

---

## D8. The memory layer is a Tier A harness policy, served from the pool at epoch 0
2026-10-12 · **proposed** (implemented: `agent_db/memory.py`)

The labmate's active branch (`tier-a-purdue`, since 2026-10-10) replaced the Omnigent host
with his own harness, whose policies can serve a tool result instead of running the tool.
Each trial is one isolated session with its own SQLite file. Cross-session memory is built as
a policy of that harness, `MemoryPolicy`, wrapping any existing policy, backed by the Agent
DB (Postgres or Neo4j), with `ingest_session` for archived sessions.

*What is served:* an identical read-only call, while this session has written nothing (write
epoch 0), from an earlier session of the same task whose result was also taken before any
write. Every trial starts from the task's container image, so such outputs are provably
identical. Nothing else is served: after a write, the file's current hash is needed and a
policy cannot see it; across tasks the containers differ.

*Evidence, from the archived Purdue run (tc9: 10 tasks x 3 repeats), replayed offline with
`experiments/replay_sessions.py`:* in the 20 later sessions, 380 tool calls, 49 read-only,
15 identical to an earlier session's call; 11 servable at epoch 0, 16,437 of 107,352
tool-output tokens (15.3%), outputs byte-identical in 11 of 11. After writes, identical calls
gave identical output 2 times and different output 2 times.

*What a served call saves:* the tool's execution. The model's prompt is unchanged, so under
Tier A's cost measure (prefill FLOPs) the saving is zero. This layer therefore measures and
reports tool time saved and provenance; whether memory can shorten trajectories (fewer calls)
is the experiment that could move FLOPs, and it needs live runs.

*Pool:* `Pool(labels, same_task, only_passed, exclude)` is the experimental condition,
named per run; every served call records its source session (D5).

*Contract impact:* none; the layer uses Part B only (`start_run`, `upsert_agent`,
`add_segment`, `add_tool_result`, `add_evidence_events`, `list_runs`, `find_tool_result`,
`end_run`, `set_resolved`). The package's record types are now its own
(`agent_db/records.py`), mirroring `econocontext.types`, so the stores import without
EconoContext installed and beside the Tier A package of the same name.
