# EconoContext

EconoContext is a cost optimizer for AI agents. It does not run agents itself. It watches
an agent that someone else runs, prices the options for each step (for example: send a
big tool result in full, or a short pointer to it), picks the cheapest correct one, and
logs every choice next to what the step really cost.

**New here?** Read this page, then [`docs/README.md`](docs/README.md) (six short chapters).
How we use Omnigent, on one page: [`docs/html/omnigent.html`](docs/html/omnigent.html).

## Who is who

| Piece | What it is | Where it lives | Ours? |
|---|---|---|---|
| **EconoContext** | The optimizer: decides, prices, logs | [`econocontext/`](econocontext/) | ✅ ours |
| **Omnigent layer** | The glue that connects Omnigent to EconoContext | [`omnigent_layer/`](omnigent_layer/) | ✅ ours |
| **Bench** | Runs SWE-bench tasks and grades them | [`bench/`](bench/) | ✅ ours |
| **Omnigent** | The platform that runs agents: sessions, tools, sandboxes, UI | installed: `pip install omnigent==0.15.0` | ❌ Databricks, open source |
| **The coding harness** | The agent loop that actually solves the task (think, call a tool, read the result, repeat) | the OpenAI Agents SDK (`openai-agents`), installed with Omnigent, chosen in [`bench/agent.yaml`](bench/agent.yaml) | ❌ OpenAI, open source |
| **The model** | Gemini 3.6 Flash | Google Vertex AI (key in `.env`) | ❌ Google |
| **SWE-bench** | Real GitHub bugs, their Docker images, the official grader | installed: `pip install swebench` | ❌ SWE-bench |

So the agent that fixes the bug is **the OpenAI Agents SDK, run by Omnigent, thinking
with Gemini**. EconoContext only watches it and, when allowed, adjusts what it sends.

## The picture

```
 bench/run.py ── starts one Omnigent session per SWE-bench task ──┐
                                                                  ▼
 ┌──────────────── Omnigent (installed, not modified) ───────────────────┐
 │  the coding harness: OpenAI Agents SDK                                 │
 │    think ──► call a tool ──► read the result ──► think again ...       │
 └──────┬──────────────────────────────────────────────┬─────────────────┘
        │ every model call                             │ every tool call and result
        ▼                                              ▼
 ┌─ omnigent_layer/gateway.py ─┐              ┌─ omnigent_layer/policy.py ─┐
 │ sees the full prompt and    │              │ sees each tool result;     │
 │ the exact tokens billed     │              │ may shorten it             │
 └──────┬──────────▲───────────┘              └──────────────┬─────────────┘
        │          └──► Gemini on Vertex                     │
        ▼                                                    ▼
 ┌──────────────────── econocontext/ (the optimizer) ─────────────────────┐
 │  list options ─► drop wrong ones ─► price them ─► pick ─► log why      │
 └────────────────────────────────┬───────────────────────────────────────┘
                                  ▼
                   Agent DB: data/econocontext.sqlite3
```

## One task, step by step

1. **`bench/run.py`** takes one bug (say `pytest-dev__pytest-5809`), copies its repository to `data/work/…`, and starts the bug's Docker image so tests can run.
2. It asks **Omnigent** to start a session with the agent in `bench/agent.yaml`.
3. **The harness** (OpenAI Agents SDK) works on the bug: it reads and edits files, and runs tests with `testbed_shell` inside the Docker image.
4. **Every model call** goes to `omnigent_layer/gateway.py`, which passes it on to Gemini and records the exact tokens. **Every tool result** goes through `omnigent_layer/policy.py`.
5. Both hand what they see to **`econocontext/`**, which decides and writes everything to the Agent DB. In `observe` mode it only logs; in `autopilot` its choices are applied.
6. When the agent is done, `bench/run.py` takes the `git diff` and **SWE-bench's official grader** says pass or fail.

## Folders

```
econocontext/     the optimizer. Imports only Python's standard library and pyyaml.
  planner/          lists the options for a step (and jev_planner.py: a slot for Jev)
  optimizer/        gates (drop wrong options) and the choice
  pricing/          the cost model, price cards, and the ledger (actual cost per call)
  learn/            labels (what happened), replay (what we'd have chosen), learned H and p
  assembler/ guard/ build the chosen request; fail safe
  monitor/ store/   what exists, and the Agent DB
omnigent_layer/   the glue, its own small package (pip install -e omnigent_layer)
  gateway.py        model calls: full prompt in, exact usage out, hard caps
  policy.py         tool calls and results
  workspace.py      which files changed (so old results are not reused)
  tools.py          testbed_shell: a shell inside the task's Docker image
  wire.py           converts the model's message format to EconoContext's
bench/            the experiment: run.py, agent.yaml (with a worker), learn.py, report.py
config/           every number the optimizer uses, and the price cards
tests/            tests for the optimizer (omnigent_layer/tests/ for the glue)
docs/             the guide, the Omnigent findings, how to run everything
v0/               the earlier prototype (archive)
```

**The rule that keeps it separate:** `econocontext/` never imports Omnigent, the
harness or any provider, so it can be worked on alone (`pytest -q` needs nothing else
installed). Only `bench/run.py` imports Omnigent. `tests/unit/test_isolation.py`
fails if either rule is broken. The earlier Deep Agents version is in git under the tag
`deepagents-host`; what we learned moving to Omnigent is in
[`docs/omnigent-findings.md`](docs/omnigent-findings.md).

## Omnigent: where to read about it

We use Omnigent **0.15.0**, pinned, because it is alpha and its event fields change
between releases.

- [Omnigent on GitHub](https://github.com/omnigent-ai/omnigent): source, README, examples (Polly, the multi-agent coding orchestrator)
- [Omnigent on Databricks](https://docs.databricks.com/aws/en/omnigent/): the managed version and quickstart
- [Introducing Omnigent](https://www.databricks.com/blog/introducing-omnigent-meta-harness-combine-control-and-share-your-agents): what a meta-harness is
- [Policies](https://github.com/omnigent-ai/omnigent/blob/main/docs/POLICIES.md): the hook `omnigent_layer/policy.py` uses
- [Agent YAML spec](https://github.com/omnigent-ai/omnigent/blob/main/docs/AGENT_YAML_SPEC.md): the format of `bench/agent.yaml`
- Ours: [how we use Omnigent, on one page](docs/html/omnigent.html) and [what we found wiring it up](docs/omnigent-findings.md)

## Details: where EconoContext decides

| Intercept | Seen at | Host default | Alternatives (exact / approximate) |
|---|---|---|---|
| `plan_prompt` | gateway | AS_IS | ZONED (exact); COMMIT_PENDING, RETRIEVE_FROM_STORE (approximate) |
| `before_tool_call` | policy | RUN_TOOL | ANSWER_FROM_STORE: logged only (a policy cannot skip a tool) |
| `admit_tool_result` | policy | KEEP_FULL | POINTER (approximate: the model must reopen the content) |
| `plan_dispatch` | — | FRESH | REUSE_RESULT; not wired yet (see findings: sub-agents) |
| `record`, `on_turn_end` | gateway | — | Measurement, per call |
| `on_file_write` | policy | — | The write barrier |

Gates run first (allowlist, quality risk, fidelity, version, side effects, window,
pairing). Survivors are priced on four terms: prepare, work, integrate, and
leaves_behind; latency is kept separate. The cheapest wins; ties go to lower latency,
then to the host default. Every loser's `why_not` is written to `decisions`. `observe`
logs decisions and changes nothing; `autopilot` applies them.

**Open for the next person**
- **`econocontext/planner/jev_planner.py`: Jev predicts whether a tool result will be needed again.** It is used only with `--jev`; see `tests/test_planner_jev.py`.

Cost tracking, runtime spans, and text/JSON/CSV reporting are documented in
[`docs/COST_TRACKING.md`](docs/COST_TRACKING.md).

## Running

Step by step, with what each step proves and costs: [`docs/TESTING.md`](docs/TESTING.md).

```sh
.venv/bin/pip install -e ".[bench,dev]" -e omnigent_layer
.venv/bin/python -m pytest -q && .venv/bin/python -m pytest -q omnigent_layer   # free
.venv/bin/omnigent start --no-open --non-interactive     # Omnigent server + runner host
.venv/bin/python -m omnigent_layer.gateway               # the gateway (localhost:8787)
.venv/bin/python bench/run.py --label dev1 --instance pytest-dev__pytest-5809 --arm econo --mode observe
.venv/bin/python bench/report.py --label dev1
```

Credentials come from `.env` (`AGENT_PLATFORM_API_KEY`). Only the gateway reads the key;
agents get a placeholder. The gateway caps every run (60 model calls) and every day
(3M input tokens). `data/` and `logs/` hold full prompts and are gitignored. Runs so far
are pipeline checks, not a savings comparison.
