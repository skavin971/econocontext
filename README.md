# EconoContext

A cost-based optimizer that plugs into an existing agent harness. The host
keeps its loop, tools and history, and makes the logical decisions (what to do
next). EconoContext makes the physical ones: how a window is sent, whether a
tool result enters in full or as a pointer, and whether a stored result can
answer a call or a delegated task. It prices each option in NU (one uncached
input token of the model in use) and logs what it predicted beside what the
provider billed. If anything fails, the host's default goes through unchanged.

The approved design is [`PLAN.md`](PLAN.md). The previous prototype (a
standalone runner, the vision pages and the September pilot) is in
[`v0/`](v0/). It is kept for reproducibility and is not imported by anything
here.

## How a call flows

```
 hosts/swebench_deepagents          adapters/deepagents               econocontext/ (core)
 ─────────────────────────          ───────────────────               ────────────────────
 Deep Agents coding agent  ──hook──► middleware.py ──types.py──► engine.py
   (Gemini on Vertex,                 wrap_model_call               │
    SWE-bench Docker image)           wrap_tool_call                ├─ monitor/   registry, cache belief
                                      after_model                   ├─ planner/   candidates per intercept (+ jev_planner with --jev)
                          ◄─decision─ (applies it with the          ├─ pricing/   rates, cost model, predictor,
                                       host's own machinery)        │             ledger (actual cost: to build)
                                                                    ├─ optimizer/ gates → price → select
                                                                    ├─ assembler/ zones + manifest (renders only)
 every model call ─────────────────► callbacks.py ──usage──► record ├─ guard/     fail-open, validation
                                      (both arms)                   └─ store/     Agent DB (SQLite + FTS5)
```

The core imports only the standard library and pyyaml. A test
(`tests/unit/test_isolation.py`) fails if it imports LangChain, Deep Agents, a
provider SDK, swebench, `adapters/` or `hosts/`. In `hosts/`, only `run.py`
(the composition root) knows EconoContext exists.

## Intercepts

| Intercept | Host default | Alternatives (exact / approximate) |
|---|---|---|
| `plan_prompt` | AS_IS | ZONED (exact); COMMIT_PENDING, RETRIEVE_FROM_STORE (approximate) |
| `before_tool_call` | RUN_TOOL | ANSWER_FROM_STORE: byte-identical, only while nothing it read has changed |
| `admit_tool_result` | KEEP_FULL | POINTER (approximate: the model must reopen the file) |
| `plan_dispatch` | FRESH | REUSE_RESULT: same task, unchanged read set, no side effects |
| `record`, `on_file_write`, `on_turn_end` | — | Measurement, the write barrier, bookkeeping |

For each decision, feasibility gates run first (allowlist, quality risk,
fidelity, version, side effects, window, pairing). The survivors are then
priced on the four terms: prepare, work, integrate, and leaves_behind. Latency
is kept separate. The cheapest wins; ties go to lower latency, then to the host
default. Every loser's `why_not` is written to `decisions`. Mode `observe` logs
the decision and returns the host default. Mode `autopilot` applies it.

## Layout

| Path | What |
|---|---|
| `econocontext/` | The core: `types.py` (the only shared language), `engine.py`, and the components above |
| `adapters/deepagents/` | Middleware, callbacks, message translation, and host executors (pointer files, `git status` write barrier) |
| `adapters/providers/` | Usage mapping: Gemini now, with Anthropic and OpenAI as stubs |
| `hosts/swebench_deepagents/` | A stock Deep Agents coding agent in the official SWE-bench image, plus the task loader and the official evaluation |
| `config/` | `econocontext.yaml` (every decision constant, with its source) and `billing_rates.yaml` (verified price cards) |
| `scripts/` | `run_baseline.sh`, `run_econo.sh`, `report.py` |
| `tests/unit/`, `tests/live/` | Plain-data unit tests; the live tests (marked `live`, paid) |

Placeholders are marked in the code: `grep -rn "# PLACEHOLDER:"` lists them.

**Open for the next person**

- **`econocontext/pricing/ledger.py`: actual cost per call and per run.**
  - It saves token counts today; the cost columns are empty.
  - The file's docstring gives the steps, and `tests/unit/test_ledger.py` holds the acceptance tests.
  - Until it is built, only the step limit caps a paid run.
- **`econocontext/planner/jev_planner.py`: Jev as the predictor of whether a tool result will be needed again.**
  - It is used only with `--jev`; without it the planner uses the fixed guess.
  - See `tests/test_planner_jev.py` for both tracks.

## Running

Full step-by-step guide, with what each step proves, what it costs and the last results: [`docs/TESTING.md`](docs/TESTING.md).

```sh
.venv/bin/pip install -e ".[host,dev]"
.venv/bin/python -m pytest -q                       # unit tests, no network
./scripts/run_baseline.sh --label dev1 --set dev    # paid: Gemini + Docker
./scripts/run_econo.sh    --label dev1 --set dev --mode observe
./scripts/run_econo.sh    --label dev1 --set dev --mode observe --jev   # planner asks Jev
.venv/bin/python scripts/report.py --label dev1
```

The scripts load credentials from `.env` (`AGENT_PLATFORM_API_KEY`); they are
never printed or committed. Every run is capped by the budgets in
`config/econocontext.yaml`. `data/` holds the Agent DB and full prompts, and is
gitignored. Runs so far are pipeline checks, not an effectiveness comparison.
