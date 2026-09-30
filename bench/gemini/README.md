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

`bench/run.py` looks for it at `data/tools/node_modules/.bin/gemini`.

## Run

The Omnigent server and the gateway must be running (restart the gateway after
changing `omnigent_layer/`):

```bash
.venv/bin/omnigent start
.venv/bin/python -m omnigent_layer.gateway
.venv/bin/python bench/gemini/smoke.py                     # one tiny task, call cap 10
.venv/bin/python bench/run.py --harness gemini-omnigent --label g1 \
    --instance pytest-dev__pytest-5809 --arm econo --mode observe
```

The API is rate limited: run one task at a time.

## How Gemini is configured (`agent.yaml`)

- `harness: acp`, with the agent embedded in the spec: `gemini --acp`,
  `omnigent_mcp: false` (Gemini's native tools only), `inject_system_prompt: false`.
- `permission_mode: bypassPermissions`: a headless session has no one to answer
  approval cards.
- Gemini runs in Vertex mode with a placeholder key. `GOOGLE_VERTEX_BASE_URL` points
  at the gateway's `/run/<run_id>/gemini` route, which is how calls are tied to a run.
  These are set in the ACP command itself because Omnigent filters the environment it
  passes to an ACP agent.
- No policy is attached: `omnigent_layer/policy.py` knows Omnigent's tool names, not
  Gemini's.

## Known limits

- Gemini's `run_shell_command` runs on the host, which does not have a SWE-bench
  repository's Python environment. Gemini can read, search and edit, but its test runs
  fail. Every Gemini arm has the same limit; its resolve rate is not comparable with
  the openai-agents path.
