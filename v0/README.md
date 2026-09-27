# EconoContext

A cost-aware agent harness. You give it a prompt, some data and a budget; it
decides where each piece of work runs, what state that work sees, and whether to
reuse a result it already has, continue a worker that already holds the context,
repair one whose state went stale, or start fresh — pricing those alternatives
before choosing.

The idea, in full, is in **[docs/vision.html](docs/vision.html)** — open it in a
browser. That page is the source of truth for what this is for; this file is the
source of truth for how to run it.

```python
import econocontext

async with econocontext.open() as eco:
    run = await eco.submit(
        prompt="Fix the failing ledger test",
        data="./repo",
        limits=econocontext.Limits(max_cost=1.00, context_tokens=32_000),
    )
    result = await eco.wait(run["id"])
```

## Running it

```sh
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock
pip install --no-deps --no-build-isolation -e .
pytest -q                              # 27 tests, no credentials, no spend
```

Then copy `.env.example` to `.env` and fill in a model endpoint and key. There is
one backend: any OpenAI-compatible Chat Completions endpoint. Google's Vertex
endpoint is reached through it too — see the commented block in `.env.example`
for the header and model-name differences it needs.

```sh
# compare both methods on one task, and record what happened
econocontext compare --fixture ledger --methods react econocontext \
  --context-tokens 1048576 --plan-pressure 0.02 --max-cost 1.00 \
  --output docs/runs/$(date +%F)-my-experiment

# what the store holds, and what it accumulated
econocontext schema

# walk one run, one component handoff at a time
econocontext run --fixture ledger --step
```

`compare` writes a walkthrough and a full trace per arm plus a cost/time summary.
Every request and response is logged in full to `logs/run-<unix>-<run_id>.txt`,
one file per run.

**`--plan-pressure` is load-bearing.** Delegation is only offered once the root
passes `context_tokens × plan_pressure`. Leave it at the 0.5 default with a large
context budget and delegation never fires — both arms run identically and you get
a null result that looks like a finding.

### The database

Nothing to set up. `data/` is created on first run: `MemoryStore.open()` applies
`store/schema.sql` with `CREATE TABLE IF NOT EXISTS` every start. It is
gitignored because it is derived state and it holds full prompts and model
output. Delete it whenever you want a clean slate.

## The repo

```
src/econocontext/
  planning/     propose candidates, price them, choose one
  store/        versioned state: evidence, plans, results + the artifact store
  runtime/      lifecycle and dispatch, the model call, accounting
  interfaces/   cli · api · explain
  assembler.py  renders the exact request; never chooses what goes in it
  agent.py      the protocol an agent implements
src/agents/
  coding/       reads a codebase, patches it, runs its tests
  research/     reads a corpus and answers from it  (thin — see its README)
  base.py       staging, workspace, subprocess, read + search
tests/          one file per owner, plus the stand-in model
docs/           vision.html · EXPERIMENTS · MEASUREMENT · DECISIONS · runs/
```

The four sub-packages match the boxes in `docs/vision.html`, so a diagram there
tells you which directory to open.

## Who owns what

| Area | Owner | Start at |
|---|---|---|
| `planning/`, `assembler.py` | planning | `tests/test_planning.py` |
| `src/agents/` | agents | [src/agents/README.md](src/agents/README.md) |
| `src/econocontext/store/` | schema | [src/econocontext/store/README.md](src/econocontext/store/README.md) |
| `runtime/`, `contracts.py`, `config.py` | shared — ask first | `tests/test_runtime.py` |

Directories do not overlap, so three people can work without colliding. Each
owned area has a README where the work is.

## Read this before quoting a number

[docs/runs/2026-09-22-no-context-cap/README.md](docs/runs/2026-09-22-no-context-cap/README.md)
records a live run where, at matched quality and with no artificial context cap,
**EconoContext cost 9.6% more than a flat ReAct agent and took 5.8s longer.**

That is the expected result on a task that fits comfortably in the window:
delegation buys an extra child call plus an integration exchange, and there is
nothing for it to save. The value of bounding a context is headroom, and headroom
only has a price near the ceiling. At a 32,000-token budget the same task was out
of reach for the flat agent entirely.

Cost only means anything at matched quality. An arm that is cheaper because it
failed is not cheaper.

## Further reading

- **[docs/EXPERIMENTS.md](docs/EXPERIMENTS.md)** — running experiments and
  reading what they print
- **[docs/MEASUREMENT.md](docs/MEASUREMENT.md)** — how cost is counted, which
  providers disagree about what, and where the unknowns are
- **[docs/DECISIONS.md](docs/DECISIONS.md)** — consequential decisions and their
  reasons
