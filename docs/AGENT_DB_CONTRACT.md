# Agent DB contract

Reference implementation: SQLite, in `econocontext/store/schema.sql`, `db.py` and `blobs.py`.

## Part A: the data

**Invariants**
- **NULL means "not reported", never zero.** Token counts and costs keep it.
- **Write-once:** `segments` and `blobs` are never updated or deleted. A repeated insert of the same key is ignored.
- **Only `labels` rows are deleted:** a run's labels are replaced as a whole.
- **Times are UTC:** `timestamptz`, ordered to the microsecond.
- **JSON columns are `jsonb`.**
- **Ids are text chosen by the application.**

### runs
One row per task run.

| column | type | null | meaning |
|---|---|---|---|
| run_id | text **PK** | | `<label>:<arm>:<instance_id>` |
| host | text | | `omnigent` |
| instance_id | text | yes | SWE-bench instance |
| arm | text | | `baseline` \| `econo` |
| mode | text | | `observe` \| `autopilot` \| `measure` |
| model | text | | |
| temperature | double | | |
| config_fingerprint | text | | |
| started_at | timestamptz | | |
| ended_at | timestamptz | yes | |
| status | text | yes | `done` \| `timeout` \| `error: …` \| `interrupted` \| `capped` |
| resolved | boolean | yes | NULL = not graded |
| jev | boolean | | default false |
| overrides | jsonb | yes | per-run config changes |
| workdir | text | yes | the run's workspace path |

### agents
| column | type | null | meaning |
|---|---|---|---|
| agent_id | text **PK** | | `<run_id>:root`, `<run_id>:worker:<8 hex>` |
| run_id | text → runs | | |
| parent_id | text → agents | yes | |
| subagent_type | text | yes | |
| status | text | | `busy` \| `idle` \| `retired` |
| window_max_tokens | integer | yes | |
| created_at, updated_at | timestamptz | | |

### segments
Every piece of context any agent saw. Write-once.

| column | type | null | meaning |
|---|---|---|---|
| segment_id | text **PK** | | sha256(agent_id \| native_id \| content_hash) |
| run_id | text → runs | | |
| agent_id | text → agents | | |
| native_id | text | | the tool-call id for tool results |
| kind | text | | `system` \| `tools` \| `task` \| `message` \| `tool_call` \| `tool_result` \| `subagent_result` |
| text | text | | the full text |
| tokens | integer | | |
| content_hash | text | | sha256(text) |
| source | text | yes | a file path, or `tool:<name>` |
| version | text | yes | |
| pinned | boolean | | |
| needs_exact_bytes | boolean | | |
| pair_id | text | yes | |
| role | text | | `system` \| `user` \| `assistant` \| `tool` |
| created_at | timestamptz | | when first seen |
| blob_key | text → blobs | yes | set when the text is 4,096 bytes or more (the same bytes, stored once) |

Needs full-text search on `text`, per run, ranked.

### blobs
Content-addressed. Write-once.

| column | type | null | meaning |
|---|---|---|---|
| blob_key | text **PK** | | sha256(data) hex |
| size | integer | | bytes |
| data | bytea | | |
| created_at | timestamptz | | |

### window_entries
What is in each agent's context now.

| column | type | null | meaning |
|---|---|---|---|
| agent_id | text → agents | | **PK** part 1 |
| segment_id | text → segments | | **PK** part 2 |
| position | integer | | |
| in_window | boolean | | |
| representation | text | | `FULL` \| `POINTER` |
| zone | text | | `FROZEN` \| `SLOW` \| `WARM` \| `VOLATILE` |
| updated_at | timestamptz | | |

### source_versions
| column | type | null | meaning |
|---|---|---|---|
| run_id | text → runs | | **PK** part 1 |
| source | text | | **PK** part 2: a path, or `*` (the workspace) |
| version | text | | an integer as text; a missing row means `"0"` |
| updated_at | timestamptz | | |

### tool_results
| column | type | null | meaning |
|---|---|---|---|
| tool_result_id | text **PK** | | equals the result's segment_id |
| run_id | text → runs | | |
| agent_id | text | | |
| tool_name | text | | |
| args_key | text | | sha256 of the arguments |
| result_segment_id | text → segments | | |
| read_set | jsonb | | `{source: version}` |
| side_effect | boolean | | |
| valid | boolean | | default true |
| created_at | timestamptz | | |

### stored_results
| column | type | null | meaning |
|---|---|---|---|
| result_id | text **PK** | | |
| run_id | text → runs | | |
| task_key | text | | |
| agent_id | text | | |
| result_text | text | | |
| read_set | jsonb | | |
| side_effect | boolean | | |
| valid | boolean | | default true |
| created_at | timestamptz | | |
| blob_key | text → blobs | yes | set when result_text is 4,096 bytes or more |

### decisions
| column | type | null | meaning |
|---|---|---|---|
| decision_id | text **PK** | | |
| run_id | text → runs | | |
| agent_id | text | | |
| intercept | text | | `plan_prompt` \| `before_tool_call` \| `admit_tool_result` \| `plan_dispatch` |
| mode | text | | |
| candidates | jsonb | | `{name: {prepare, work, integrate, leaves_behind, latency_ms}}` |
| feasible | jsonb | | `[name]` |
| chosen | text | | |
| applied | boolean | | |
| predicted_cost | jsonb | | |
| why_not | jsonb | | `[{name, why_not}]` |
| cache_predicted | integer | yes | |
| manifest_hash | text | yes | |
| decision_ms | double | | |
| error | text | yes | |
| created_at | timestamptz | | |
| prediction | jsonb | yes | |
| subject_id | text | yes | what the decision was about |
| payloads | jsonb | yes | each candidate's sizes |

### outcomes
One row per model call.

| column | type | null | meaning |
|---|---|---|---|
| outcome_id | text **PK** | | |
| run_id | text → runs | | |
| agent_id | text | | |
| decision_id | text | yes | |
| phase | text | | `agent` \| `compaction` |
| uncached_input, cache_read, cache_write, output, reasoning | integer | yes | NULL = not reported |
| latency_ms | double | yes | |
| cost_nu, cost_usd | double | yes | |
| cost_complete | boolean | | |
| price_period | text | yes | |
| raw | jsonb | | |
| created_at | timestamptz | | |

### runtime_spans
| column | type | null | meaning |
|---|---|---|---|
| span_id | text **PK** | | |
| run_id | text → runs | | |
| agent_id | text | | |
| kind | text | | `model` \| `tool` \| `dispatch` |
| name | text | | |
| native_id | text | yes | |
| decision_id | text | yes | |
| started_at | timestamptz | | |
| ended_at | timestamptz | yes | |
| duration_ms | double | yes | |
| status | text | | `open` \| `completed` \| `failed` |
| metadata | jsonb | | default `{}` |

### labels
Written after a run.

| column | type | null | meaning |
|---|---|---|---|
| run_id | text | | **PK** part 1 |
| kind | text | | **PK** part 2: `decision` \| `result` |
| subject_id | text | | **PK** part 3 |
| agent_id | text | yes | |
| tool_name | text | yes | |
| h_actual | integer | yes | |
| arrived_call | integer | yes | |
| needed_calls | jsonb | yes | |
| refetched | boolean | yes | |
| referenced | boolean | yes | |

### gateway_pointers
Omnigent layer.

| column | type | null | meaning |
|---|---|---|---|
| run_id, agent_id, tool_call_id | text | | **PK** |
| text | text | | |

**Indexes:**
- `segments (run_id, source)`
- `segments (agent_id, kind, created_at)`
- `window_entries (agent_id, in_window, position)`
- `tool_results (run_id, tool_name, args_key, valid)`
- `stored_results (run_id, task_key, valid)`
- `decisions (run_id, intercept)`
- `outcomes (run_id, agent_id)`
- `outcomes (agent_id, created_at)`
- `outcomes (created_at)`
- `runtime_spans (run_id, started_at)`
- `runtime_spans (run_id, status, agent_id)`
- full-text on `segments.text`

## Part B: the API

### Blobs
| operation | returns / effect |
|---|---|
| `blobs.put(data: bytes) -> blob_key` | blob_key = sha256(data); storing identical bytes again is a no-op |
| `blobs.get(blob_key) -> bytes` | error if the key is unknown |

### Runs
| operation | returns / effect |
|---|---|
| `start_run(run_id, host, instance_id, arm, mode, model, temperature, fingerprint, jev, overrides, workdir)` | insert; ignore if it exists |
| `end_run(run_id, status)` | |
| `set_resolved(run_id, resolved)` | |
| `get_run(run_id)` | the row, or none |
| `list_runs(label)` | runs whose id starts with `<label>:` |
| `labelled_runs(exclude_instance)` | ids of runs with labels, except one instance |

### Agents and context
| operation | returns / effect |
|---|---|
| `upsert_agent(run_id, agent)` | insert, or update status and updated_at |
| `add_segment(segment)` | insert; ignore if it exists; put large text in blobs and set blob_key |
| `segment(segment_id)` | the segment, or none |
| `set_window(agent_id, segments)` | atomic: the agent's window becomes exactly these, in order |
| `window(agent_id)` | the in-window segments, by position |
| `first_segment(agent_id, kind)`, `segments_of(agent_id, kind)` | |
| `assistant_segments(run_id)` | ordered by created_at |
| `search_segments(run_id, terms, limit)` | best matches first |

### Versions, reuse, dependents
| operation | returns / effect |
|---|---|
| `current_versions(run_id)` | `{source: version}` |
| `bump(run_id, sources)` | each source and `*`: version += 1; then `invalidate` |
| `invalidate(run_id)` | valid = false where read_set no longer matches current versions |
| `dependents(run_id, source)` | `{tool_results: [ids], stored_results: [ids], workers: [agent_ids]}`: the results whose read_set names the source, and the workers that read it. Paths are normalized |
| `add_tool_result(...)`, `find_tool_result(run_id, tool_name, args_key)` | latest valid, side-effect-free match, with its text |
| `add_stored_result(...)`, `find_stored_result(run_id, task_key)` | the same; large result_text also goes into blobs |
| `tool_results_with_text(run_id)`, `read_sizes(run_id)`, `count_tool_results(run_id)` | |

### Snapshot
| operation | returns / effect |
|---|---|
| `snapshot(run_id)` | `{run, versions, agents, windows: {agent_id: [segment_id]}, valid_tool_results, valid_stored_results, taken_at}`, read together (one transaction) |

`scoped()` is deferred until the concurrent-worker and security work.

### Decisions, outcomes, spans
| operation | returns / effect |
|---|---|
| `add_decision(run_id, agent_id, decision, mode, decision_ms, error, cache_predicted, manifest_hash)` | |
| `decisions_of(run_id)`, `decision_summary(run_id)`, `replay_rows(run_id)` | |
| `add_outcome(...)` | insert; ignore if it exists |
| `outcomes_of(run_id)`, `outcome_totals(run_id)`, `calls_by_agent(run_id)` | |
| `calls_so_far(agent_id)` | |
| `call_count_at(agent_id, segment_id)` | outcomes strictly before the segment's created_at |
| `cache_share(run_id)` | cache_read ÷ (uncached + cache_read), or none |
| `predicted_vs_actual(run_id)`, `worker_calls(run_id)`, `last_prompt_tokens(agent_id)`, `input_tokens_since(time)` | |
| `add_runtime_span(...)` | insert as open; ignore if it exists |
| `finish_runtime_span(span_id, duration_ms, status)` | |
| `spans_of(run_id, kind?)`, `has_open_model_span(agent_id)`, `count_model_spans(run_id)` | |

### Labels, pointers
| operation | returns / effect |
|---|---|
| `replace_labels(run_id, rows)` | atomic: delete the run's labels, insert these |
| `result_labels(run_id)` | |
| `add_gateway_pointer(run_id, agent_id, tool_call_id, text)` | upsert |
| `gateway_pointers(run_id, agent_id)` | `{tool_call_id: text}` |
