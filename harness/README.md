# The harness: agents on Omnigent

Harnesses: **Claude Code** (`specs/claude_code/`, the default), Gemini CLI
(`specs/gemini/`) and our openai-agents spec (`specs/openai_agents.yaml`).
`session.py` runs any of them; `benchmarks/<name>/run.py` supplies the tasks.

## Claude Code

Install once: `npm install -g @anthropic-ai/claude-code@2.1.286`. Its model calls go to
the gateway's `/run/<id>/anthropic` route; the Anthropic key lives only in `.env`
(`ECONOCONTEXT_ANTHROPIC_KEY`), with a total budget (`ECONO_ANTHROPIC_BUDGET_USD`, default
$4.50) and a per-run budget (`limits.per_instance_budget_usd`) enforced at the gateway.

```bash
.venv/bin/python harness/smoke.py                      # Claude Code, one tiny task
.venv/bin/python benchmarks/swebench/run.py --label c3 --instance pytest-dev__pytest-5809 --arm econo
```

| Capability on Claude Code | Status |
|---|---|
| Every model call: tokens, cache reads, 5-minute and 1-hour cache writes, cost | yes (gateway); only `claude-sonnet-5` is allowed through |
| Tool calls and results per call, versioned evidence, labels, oracle | yes (gateway) |
| EconoContext's planner on each request | yes: every call gets a decision (observe logs it; autopilot may carry it out) |
| Carrying decisions out | autopilot: COMMIT_PENDING only (old tool results → pointers, kept stable); never reordering; falls back to the original if Anthropic refuses a changed request |
| Tool events in Omnigent policies | yes (`PreToolUse`/`PostToolUse`); a policy can deny, not replace a result |
| Sub-agents (`Agent` tool) | seen by Omnigent (child session per agent id) and the gateway (own `context_key`); continued with context and cache via `SendMessage` (probe); steering by EconoContext not built yet |

# Gemini CLI on Omnigent

Stock Gemini CLI, run by Omnigent over ACP, with every model call passing the
EconoContext gateway. Gemini decides what to do (its own planner, tools and built-in
sub-agents); Omnigent runs it; EconoContext only measures it in this milestone.

```text
Gemini CLI --ACP--> Omnigent (session, permissions, lifecycle)
    |
    +--model calls--> gateway /run/<run_id>/gemini --> Vertex (real key added here)
```

## Install (once)

```bash
npm install --prefix data/tools @google/gemini-cli@0.62.0   # the pinned version
```

`benchmarks/swebench/run.py` looks for it at `data/tools/node_modules/.bin/gemini`.

## Run

The Omnigent server and the gateway must be running (restart the gateway after
changing `omnigent_layer/`):

```bash
.venv/bin/omnigent start
.venv/bin/python -m omnigent_layer.gateway
.venv/bin/python harness/smoke.py                     # one tiny task, call cap 10
.venv/bin/python benchmarks/swebench/run.py --harness gemini-omnigent --label g1 \
    --instance pytest-dev__pytest-5809 --arm econo --mode observe
```

Afterwards, offline (no model calls):

```bash
.venv/bin/python harness/learn.py label  --label g1   # which reads were repeated, and needlessly
.venv/bin/python harness/learn.py oracle --label g1   # model turns that only fetched evidence
.venv/bin/python harness/report.py --label g1
```

`probe.py` asks, with one natural task, whether Gemini's own sub-agents are visible to
and addressable by Omnigent (answers in `docs/omnigent-findings.md`):

```bash
.venv/bin/python harness/probe.py --repo data/work/g1_econo_pytest-dev__pytest-5809
```

The API is rate limited: run one task at a time.

## How Gemini is configured (`agent.yaml`)

- `harness: acp`, with the agent embedded in the spec: `gemini --acp`,
  `omnigent_mcp: false` (Gemini's native tools only), `inject_system_prompt: false`.
- `--approval-mode yolo` (Gemini's own setting): a headless session has no one to answer
  approval cards, and an Omnigent card would end the turn. Omnigent still sees every
  ACP `tool_call`.
- A per-run `HOME` whose `.gemini/settings.json` (from `harness/specs/gemini/settings.json`)
  selects Vertex auth: in ACP mode Gemini reads the auth type only from settings.
- Gemini runs in Vertex mode with a placeholder key. `GOOGLE_VERTEX_BASE_URL` points
  at the gateway's `/run/<run_id>/gemini` route, which is how calls are tied to a run.
  These are set in the ACP command itself because Omnigent filters the environment it
  passes to an ACP agent.
- No policy is attached: `omnigent_layer/policy.py` knows Omnigent's tool names, not
  Gemini's.

## Known limits

- One-time setup: `.venv/bin/python harness/setup.py` also registers Gemini CLI as
  an ACP agent. Omnigent's host daemon refuses the `acp` harness without one.
- Gemini's `run_shell_command` runs on the host, which does not have a SWE-bench
  repository's Python environment. Gemini can read, search and edit, but its test runs
  fail (on pytest-5809 it looked for pytest with `find / -name pytest`). Every Gemini arm
  has the same limit; its resolve rate is not comparable with the openai-agents path.

## What is supported on Gemini (capability matrix)

| Capability | Status | Where from |
|---|---|---|
| Run stock Gemini CLI (own planner, tools, sub-agents) | yes | Omnigent `acp` harness |
| Every model call: tokens, cache, thinking, latency | yes | gateway (`usageMetadata`) |
| Cost per call | yes for `gemini-3.6-flash`; incomplete for models without a verified price card (the sub-agent's `gemini-3.8-flash`) | ledger |
| Tool calls and results, per call (names, sizes, hashes) | yes | gateway (request history) |
| Versioned evidence, reacquisition labels, acquisition-turn oracle | yes (econo arm) | gateway → `evidence`, `labels`, `learn.py oracle` |
| Tool events in Omnigent's transcript | yes, named by ACP title | ACP |
| Governing Gemini's tools through `policy.py` | no: Gemini runs its own tools | — |
| Sub-agent calls told apart | yes, by `context_key` and model | gateway |
| Sub-agents as Omnigent sessions (list, continue, resume, redirect) | **no** (probe, Outcome B): worker placement unsupported | — |
| Changing what Gemini sees (overlay, pointers) | not in this milestone | — |

Details and evidence: `docs/omnigent-findings.md`. What is recorded per call:
`docs/gemini-measurement-schema.md`.
