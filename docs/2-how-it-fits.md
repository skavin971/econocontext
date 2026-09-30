# 2. How it fits into an agent

## Three parts, one rule about imports

```
harness/          how an agent runs on Omnigent, whatever the benchmark
benchmarks/       one folder per benchmark: swebench/ runs SWE-bench instances as Omnigent sessions
        │  starts sessions; registers runs and reads reports from the Agent DB
        ▼
omnigent_layer/   the platform layer: an Omnigent policy and a model gateway
        │  the only code that knows both Omnigent and EconoContext
        ▼
econocontext/     the optimizer: all decisions. Imports only the stdlib and pyyaml
```

- **Why the optimizer is kept apart:** it can be developed and tested alone (`pytest -q`, no Omnigent installed), and a second platform would need only its own `<platform>_layer/`. `tests/unit/test_isolation.py` fails if the core imports anything but the standard library and pyyaml, or if anything but `benchmarks/swebench/run.py` imports Omnigent.
- **One shared language:** the core's parts talk to each other only through the dataclasses in `econocontext/types.py`, such as `Segment`, `Candidate`, `Decision` and `ProviderUsage`.
- **Why Omnigent:** it can run many harnesses (Claude Code, Codex, Pi, the OpenAI Agents SDK, …), with sessions, sub-agents, sandboxes and a UI. EconoContext adds only what is new: pricing and choosing.
- **The harness we actually use:** the **OpenAI Agents SDK** (Omnigent's `openai-agents` harness), thinking with Gemini 3.6 Flash. It is the agent loop that solves the SWE-bench task. It is installed with Omnigent, not part of this repo, and chosen in `harness/specs/openai_agents.yaml`. The other harnesses need Anthropic or OpenAI keys we don't have yet.

## The core, module by module

| Module | Job | Real or placeholder |
|---|---|---|
| `engine.py` | The front door. One method per intercept; runs the pipeline below and logs the decision | Real |
| `monitor/registry.py` | Keeps the agent tree and each agent's current window; writes both to the DB | Real |
| `monitor/cache_belief.py` | Guesses how many tokens the provider will serve from cache, then compares with what it reported | Placeholder |
| `planner/candidates.py` | The catalog of operators: 13 named, 10 generated today | Real |
| `planner/planner.py` | Proposes candidates for one step, with fixed rules | Placeholder rules |
| `planner/jev_planner.py` | A slot for Jev to predict whether a tool result will be needed again (only with `--jev`) | Empty, to build |
| `optimizer/gates.py` | 7 correctness checks | Real |
| `optimizer/optimizer.py` | Gates, then price, then choose; records `why_not` for every loser | Real |
| `pricing/cost_model.py` | Predicts a candidate's cost as four terms | Placeholder: ignores caching |
| `pricing/predictor.py` | Fixed guesses: turns remaining, and whether content is needed again | Placeholder |
| `pricing/rates.py` | Turns a price card into NU ratios | Real |
| `pricing/ledger.py` | Actual cost per call and per run, from the provider's reported usage | Real, tested |
| `assembler/` | Orders the request into zones and writes a manifest (a fingerprint of what was sent) | Real |
| `guard/` | Fail-open wrapper, and final validity checks | Real (deadline not enforced) |
| `store/` | The Agent DB: SQLite schema, reads and writes, keyword search | Real (search is keyword-only) |
| `config.py`, `tokens.py`, `host.py` | Load the YAML config; count tokens (characters ÷ 4); the protocol a host implements | Real (token count is an estimate) |

## Two taps: where EconoContext sees the agents

Omnigent is not modified. EconoContext sees the agents through two things Omnigent already offers:

| Tap | What it is | What it sees | What it can change |
|---|---|---|---|
| **Gateway** (`omnigent_layer/gateway.py`) | A local OpenAI-compatible server. The agent's model URL points at it; it forwards to Vertex with the real key | Every model call of every agent: the full prompt, and the exact usage including cached tokens | The request (econo arm only) |
| **Policy** (`omnigent_layer/policy.py`) | An Omnigent policy, attached in the agent spec | Every tool call and tool result | A tool result, before the model sees it |

Why two: Omnigent's policy events carry only a summary of each model call (never the prompt) and no per-call usage, so model calls are tapped at the gateway (see [the findings](omnigent-findings.md)).

## The intercepts: where each one is seen

| Intercept | Seen at | When | What it can change |
|---|---|---|---|
| `plan_prompt` | gateway | Before every model call | The order and content of the request |
| `record` | gateway | After every model call, from the provider's usage | Nothing: it measures |
| `on_turn_end` | gateway | After every model response | Nothing: it counts turns |
| `before_tool_call` | policy | Before a tool runs | Nothing yet: a policy cannot skip a tool, so a would-be answer from the store is only logged |
| `admit_tool_result` | policy | After a tool runs, before its result reaches the model | Full result, or a pointer |
| `on_file_write` | policy | After `sys_os_write`, `sys_os_edit`, `sys_os_shell` or `testbed_shell` | Nothing: `git status` finds what changed, so stale stored results are not reused |
| `plan_dispatch` | — | Before a sub-agent starts | Not wired yet: sub-agents do not run on our Gemini setup (findings, question 4) |

## Two modes, and the baseline

- **Observe** (the default in config): every decision is computed and logged, but Omnigent's own behavior is always carried out. Use this first for any change.
- **Autopilot**: decisions are carried out. By default only *exact* operators are allowed:
  - `config/econocontext.yaml` sets `max_quality_risk: 0.0`
  - `POINTER`, `COMMIT_PENDING` and `RETRIEVE_FROM_STORE` are switched off in `allowlist`
- **Baseline** runs are `mode = measure`: the gateway forwards them byte for byte and only measures; no policy is attached.

## One run, start to finish

This is `benchmarks/swebench/run.py`, for one SWE-bench instance:

1. **Register the run** in the Agent DB (`omnigent_layer.register_run`): arm, mode, model, config fingerprint, and whether `--jev` is on. From now on the gateway and the policy know the run from its id alone.
2. **Prepare the workspace:** copy `/testbed` out of the instance's official image into `data/work/<run>`, then start that image with the copy mounted at `/testbed`, so tests run in the repository's own environment.
3. **Write the agent spec** from `harness/specs/openai_agents.yaml`: the model URL is `http://127.0.0.1:8787/run/<run_id>/v1`, the working directory is the workspace, and the econo arm gets the EconoContext policy. This is the only difference between the arms.
4. **Start an Omnigent session** and send the issue text. The agent reads and edits with Omnigent's file tools, and runs tests with `testbed_shell` (`omnigent_layer/tools.py`), which runs in the container.
5. **Stop** when the agent says it is done, when the gateway's cap (60 model calls) refuses a call, or on an error.
6. **Take the patch** (`git diff` of the workspace) and write it to `data/runs/<label>/`.
7. **Grade it** with the official SWE-bench harness in a fresh container. The result is written back to `runs.resolved`.
8. **Report:** `harness/report.py --label <label>`.

## Agents

- **Agent ids:** the main agent is `<run_id>:root`. A sub-agent given its own model URL `.../run/<run_id>/agent/<name>/v1` is `<run_id>:<name>`.
- **Every agent has its own window, turn count and read set** (the files it read, with their versions). All of these are recorded.
- **Tool events** reach the root agent's policy even when a sub-agent made them (Omnigent evaluates only the root's policies), so they are recorded under the root.

## Fail-open, precisely

Every intercept's decision runs inside `guard.fail_open.guarded(...)`:

- **An exception** is logged, the host default is returned, and a decision row is still written with the error.
- **Omnigent's own work** (running a tool, calling the model) is never inside the guard. If it fails, that failure belongs to Omnigent and is not hidden.
- **Outside the engine too:** the policy abstains on any error, and the gateway forwards the harness's own request if planning fails. Only the gateway's caps ever refuse a call.
- **Before the request is sent**, the rendered request is checked by `guard/validate.py`:
  - every tool call is directly followed by its results
  - no pinned segment was dropped
  - it fits the window

  If any check fails, the harness's own request is sent instead.
- **Decision time** is measured against a 50 ms deadline and logged. It is not yet enforced.

Next: [3. How a decision is made](3-how-a-decision-is-made.md)
