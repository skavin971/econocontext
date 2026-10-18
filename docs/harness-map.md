# Harness map (step 11)

**What step 11 does:**
- Our own harness, `harness/`, runs one loop. A policy, chosen by `--policy`, is the only part that differs between arms.
- The behaviour of 10c and 10d is kept unchanged, but moves out of the agent into the policies.
- Removed files remain at tag `tier-a-step9` and in this branch's history.

## Moves (`git mv`)

| From | To | Reason |
|---|---|---|
| `agents/react/agent.py` | `harness/agent.py` | The loop: model calls, the policy hooks, the budget gate, ending, logging (`trajectory.json`). |
| `agents/react/prompts/clm_final_turn.txt` | `harness/prompts/clm_final_turn.txt` | The budget gate (CLM's final turn) is part of the harness, the same for every policy. |
| `agents/react/prompts/codex_compact.md`, `codex_summary_prefix.md`, `LICENSE-codex`, `NOTICE-codex` | `baselines/summary/` | The Codex files go with the Summary baseline. |
| `tests/agents/test_react_agent.py` | `tests/harness/test_agent.py` | The tests follow the code. |

## New files

| File | What it holds |
|---|---|
| `harness/interface.py` | What the model sees, the same for every policy: system prompt, tools, nudges, the one-tool-call rule. Two interfaces: `ours` (new, the default) and `legacy` (steps 9–10, to reproduce old runs). There is no context-management text in either. |
| `harness/tools.py` | The four tools run in the container (bash, read_file, write_file; submit ends the run) and the output cap. These are split out of `agent.py`. |
| `harness/budget.py` | CLM's budget gate (10c): over budget − 2,048, one final turn with CLM's notice, then stop. Back under the limit re-arms it. |
| `baselines/base/policy.py` | Base (10c): no context management. The budget gate does the rest. |
| `baselines/summary/policy.py` | Summary (10d): Codex compaction at 75% of the budget, unchanged. It needs a budget. |
| `econocontext/policy.py` | `Econo`, which wraps `Owner` with no rule change and refuses a budget. `Directed` is a stub that refuses to run. |
| `tests/harness/test_interface.py`, `tests/baselines/`, `tests/econocontext/test_policy.py` | Every policy sends the identical prompt and tools; one tool call per reply; the nudges; the legacy interface; the policies. |

## Removed

| Path | Reason |
|---|---|
| `agents/__init__.py`, `agents/react/__init__.py` | `agents/` is replaced by `harness/`. |

## Wiring and rules

**How a policy is wired in.** The runner maps `--policy` to a class path, which the harness imports by name; the harness itself names no policy package. Every policy has the same hooks:
- `start(instruction, harness)`;
- `before_call(messages, harness)`, which may edit the conversation in place and returns what to send;
- `before_tool` and `after_tool`;
- `report()`.

Each policy also declares whether a budget is allowed, required or refused.

**The policies `--policy` can choose:**
- `base` and `summary` (`baselines/`);
- `econo+jev` and `econo+prior` (`econocontext/`);
- `directed`, which refuses to run;
- `clm` (`baselines/clm/run.sh`, unchanged).

**Other options.** `--interface ours|legacy` picks the interface. Arms with a budget keep the naming `<policy>-<N/1000>k`.

**Isolation tests:**
- `harness/` imports neither `econocontext/` nor `baselines/`, and does not name them.
- `econocontext/` never imports `baselines/`.
- `measure/` never imports `econocontext/` (as before).
- No provider host or key appears in `harness/` or in our part of `baselines/` (not `*/upstream/`).
