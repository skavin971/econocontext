# 3. How a decision is made

Every decision goes through the same five steps:

```
planner (propose) → gates (remove wrong ones) → cost model (predict) → optimizer (choose) → assembler (render)
```

This chapter walks through each step with the exact rules and numbers used today.
Almost every number below is a **fixed guess from config**. Nothing is learned yet.
Chapter 6 covers what replaces these guesses.

## Step 1: the planner proposes candidates

The **operator catalog** (`planner/candidates.py`) names every way a step can be
done. The **planner** (`planner/planner.py`) picks which ones to propose for a
given step, using fixed rules. The host default is always proposed.

| Intercept | Always proposed | Also proposed when… | Typical count today |
|---|---|---|---|
| `plan_prompt` | `AS_IS` (host default), `ZONED` | `RETRIEVE_FROM_STORE`: switched on in config **and** a keyword search finds stored segments not in the window. `COMMIT_PENDING`: pointer edits are queued **and** the cache is believed cold; nothing queues them yet, so it is never proposed | 2 |
| `before_tool_call` | `RUN_TOOL` (host default) | `ANSWER_FROM_STORE`: the tool has no side effects **and** an earlier call to the same tool had byte-identical arguments (same SHA-256 of name + arguments) **and** that result is still marked valid | 1 or 2 |
| `admit_tool_result` | `KEEP_FULL` (host default) | `POINTER`: the result is at least 2,000 tokens (`planner.pointer_min_tokens`) | 1 or 2 |
| `plan_dispatch` | `FRESH` (host default: start a new subagent) | `REUSE_RESULT`: an earlier subagent was given the same task (same subagent type + description, whitespace-normalized) and its result is still valid | 1 or 2 |

**What each operator means**

- **AS_IS**: send the request exactly as the host built it.
- **ZONED**: the same content, reordered into four zones, from most to least stable:
  - `FROZEN`: system prompt and tool list
  - `SLOW`: the task
  - `WARM`: older history
  - `VOLATILE`: the newest turn

  Tool calls and their results never move. In a typical harness conversation this order is almost always the same as AS_IS already.
- **RETRIEVE_FROM_STORE** (approximate): append up to 5 stored segments that match keywords in the newest message, at the end of the prompt.
- **COMMIT_PENDING** (approximate): swap queued pointer edits into the prompt, only when the prefix isn't cached anyway.
- **ANSWER_FROM_STORE** (exact): return the earlier output byte for byte. The agent can't tell the difference, and the reuse is recorded only in the DB.
- **POINTER** (approximate): put the first 5 and last 5 lines in the window, plus: "Full output: N tokens, saved at /tmp/econocontext/<id>.txt. Read it with your file tool if you need more." The full text is saved where the agent's own `read_file` can open it. (On Omnigent this is not wired yet: it needs a place in the workspace the agent can reopen.)
- **REUSE_RESULT** (exact): return the earlier subagent's answer byte for byte.
- **Named but never proposed**, because the host can't do them yet:
  - `RESUME`: continue a live subagent that already has the context
  - `FORK`: a subagent that inherits the parent's context
  - `REPAIR`: refresh only what changed in a worker's state

## Step 2: gates remove incorrect candidates

`optimizer/gates.py` runs these checks in order. The first failure removes the
candidate, and the reason is recorded as `why_not`, e.g. `"allowlist: operator not
enabled in the allowlist"`.

| Gate | Removes a candidate when… |
|---|---|
| `allowlist` | It isn't switched on in `config/econocontext.yaml` → `allowlist`, or the host lacks the capability for it (e.g. it can't write pointer files). The host default always passes |
| `quality` | Its quality risk is above `max_quality_risk`. Exact operators have risk 0. Guessed risks: POINTER 0.2, COMMIT_PENDING 0.2, RETRIEVE 0.5. With the default of 0.0, every approximate operator is removed |
| `fidelity` | It's a POINTER or COMMIT_PENDING on content that needs exact bytes |
| `version` | It reuses a stored result, and any file it read has changed since (see "Versions" below) |
| `side_effects` | It reuses a result that came from a call that changed something |
| `window` | The request would be bigger than the model's window (1,048,576 tokens for Gemini 3.6 Flash) |
| `pairing` | It's ZONED, and reordering would separate a tool call from its results |

### Versions: how stale results are caught

- **Version counters:** every file path has a version, starting at `"0"`, and so does the workspace as a whole (`"*"`). They live in the `source_versions` table.
- **The read set:** a stored tool result records what it read, and at which version:
  - `sys_os_read` reads its own path
- **The write barrier** (`on_file_write`): after a change, it bumps the version of that path and of `"*"`, then marks every stored result whose read set no longer matches as `valid = 0`.
  - After `write_file`, `edit_file` or `delete`, the change is that path.
  - After `sys_os_write`, `sys_os_edit`, `sys_os_shell` or `testbed_shell`, `git status` in the workspace finds which files actually changed (`omnigent_layer/workspace.py`). If git can't answer, only `"*"` is bumped.
- **Never reused:** results of side-effecting tools (the four above).

## Step 3: the cost model predicts each candidate's cost

`pricing/cost_model.py` predicts four terms, in NU (1 NU = one uncached input token).
Each candidate carries sizes (its *payload*); the formula is the same for all of them:

```
prepare       = tokens sent now
              + p_need_again × tokens that must be re-read later        (POINTER only)
              + extra model calls × input tokens per call                (FRESH subagent only)
work          = model calls × 345 expected output tokens × 5             (output costs 5 NU/token)
integrate     = tokens the result adds to the window
leaves_behind = tokens left in the window × remaining turns

total = prepare + work + integrate + leaves_behind        (latency is kept separate)
latency_ms = 1,400 per model call + 32 if a tool runs + 1 if read from the store
```

- **Why `leaves_behind` matters most:** anything left in the window is re-sent on every remaining turn. A 3,000-token result kept for 13 more turns costs 39,000 NU over the rest of the run.
- **Caching is ignored:** every input token counts as uncached (1 NU), because the cache-aware version isn't built yet. So predictions run high: on the first dev run, the prediction was 247K NU against 118K NU actually billed.

### Every prediction is a fixed guess today

| What is predicted | How (today) | Where |
|---|---|---|
| Tokens in a piece of text | characters ÷ 4, rounded up | `tokens.py` |
| Output tokens per model call | 345 (the average over 618 calls in the v0 pilot) | `cost_model.expected_output_tokens` |
| Turns left for this agent | 18 − turns taken so far, minimum 1 (18 = half the median run length in the v0 pilot) | `predictor.remaining_turns_default` |
| **Will this content be needed again?** (`p_need_again`, 0 to 1) | A fixed number per kind: system, tools and task 1.0; message 0.5; tool call 0.3; **tool result 0.3**; subagent result 0.5. Above 2,000 tokens it is multiplied by 2,000 ÷ tokens (a 10,000-token result gets 0.3 × 0.2 = 0.06). Pinned or exact-bytes content gets 1.0. With `--jev`, the planner asks `jev_planner.py` instead (not built yet) | `predictor.kind_need_again`, `pricing/predictor.py` |
| Cost of a new subagent | 5 model calls × 8,950 input tokens each (v0 pilot medians) | `cost_model.fresh_expected_*` |
| Latency | 1,400 ms per model call (median of 10 probe calls); tool 32 ms; store 1 ms | `latency` |
| Cached tokens on the next call | The longest shared prefix with the agent's previous request, if that was sent within 300 s and the prefix is at least 4,096 tokens. **Logged, but not used in the price yet** | `monitor/cache_belief.py` |

`p_need_again` only affects the POINTER candidate. Because POINTER is switched off,
it changes no decision today. It is priced and logged, so it can be evaluated.

## Step 4: the optimizer chooses

`optimizer/optimizer.py`, with `objective: cost` (the default):

1. Drop candidates that failed a gate.
2. Drop candidates above `max_cost_nu` or `max_latency_ms`. Both are `null` (no limit) by default.
3. Pick the lowest total NU. **On a tie:** lower latency wins; if still tied, the host default wins.
4. If nothing is left, pick the host default.
5. Record every loser's `why_not`, e.g. `"not the best under objective cost: 53445.0 NU / 7000 ms vs 70.0 NU / 1 ms"`.

The other objectives are `latency` (fastest, then cheapest) and `balanced` (NU + `latency_weight` × ms, where the weight must be set in config).

## Worked examples (real output of the code, for an agent at turn 5)

**Model call, 15,000-token window.**
- AS_IS and ZONED both cost 15,000 + 1,725 + 195,000 = 211,725 NU, at 1,400 ms.
- It's a tie, so the host default (AS_IS) wins. ZONED's `why_not`: "not the best".

**A 3,000-token file read arrives.** POINTER is proposed because the result is at least 2,000 tokens:

| | prepare | integrate | leaves_behind | total |
|---|---|---|---|---|
| KEEP_FULL | 0 | 3,000 | 3,000 × 13 = 39,000 | 42,000 |
| POINTER (p_need_again 0.2) | 0.2 × 3,000 = 600 | 233 | 233 × 13 = 3,029 | 3,862 |

POINTER is far cheaper, but it is removed by the `allowlist` gate, so KEEP_FULL is chosen.

**The same file is read again, unchanged.**
- RUN_TOOL and ANSWER_FROM_STORE both cost 42,000 NU, because the result is the same size.
- It's a tie on cost, and the store is faster (1 ms vs 32 ms), so ANSWER_FROM_STORE wins.
- On the earlier Deep Agents host, autopilot then skipped the tool (6 times in the 5-instance check). On Omnigent a policy cannot skip a tool, so this is logged only.

**The same subagent task is requested again.**
- FRESH = 5 × 8,950 + 5 × 345 × 5 + result ≈ 53,445 NU, at 7,000 ms.
- REUSE_RESULT = 70 NU, at 1 ms.
- REUSE_RESULT wins, if nothing the first subagent read has changed.

## Step 5: the assembler renders, and never chooses

`assembler/assembler.py` builds the exact request for the chosen candidate:

- **Zones:** each segment gets a zone, and the request is reordered only if ZONED was chosen.
- **Retrieval:** retrieved segments are appended at the end.
- **Manifest:** it writes a SHA-256 fingerprint of what was sent. That covers the zone hashes, segment ids, file versions, estimated tokens and config fingerprint. Identical inputs always give an identical manifest.
- **Cache breakpoint:** it records a cache breakpoint (after the last stable segment), for providers that need explicit cache markers. Gemini caches automatically, so it isn't used yet.

Then `guard/validate.py` checks the result (chapter 2). The adapter sends either this
request or, in observe mode or on any problem, the host's original.

Next: [4. Measurement and data](4-measurement-and-data.md)
