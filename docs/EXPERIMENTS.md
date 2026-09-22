# Running the experiments

How to reproduce what EconoContext does, read what it prints, and judge whether
it worked.

## Setup

```sh
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock
pip install --no-deps --no-build-isolation -e .
pytest -q          # 27 passed, no credentials, no spend
```

Then copy `.env.example` to `.env` and fill it in. There is one backend: any
OpenAI-compatible Chat Completions endpoint. For Google's Vertex endpoint,
verified working:

```sh
ECONOCONTEXT_BACKEND=live
ECONOCONTEXT_BASE_URL=https://aiplatform.googleapis.com/v1/projects/PROJECT/locations/global/endpoints/openapi
ECONOCONTEXT_MODEL=google/gemini-3.5-flash
ECONOCONTEXT_AUTH_HEADER=x-goog-api-key      # NOT Authorization: Bearer
ECONOCONTEXT_AUTH_SCHEME=
ECONOCONTEXT_OUTPUT_PARAMETER=max_tokens
ECONOCONTEXT_LIVE_CONTEXT_TOKENS=1048576
ECONOCONTEXT_CREDENTIAL_ENV=MY_API_KEY
MY_API_KEY=...
ECONOCONTEXT_PRICING={"revision":"2026-09-16","input_per_million":1.50,"cached_per_million":0.15,"output_per_million":9.00}
ECONOCONTEXT_CALL_LOG=./logs
```

Sanity check before spending properly:

```sh
econocontext run --fixture corpus --adapter research --max-cost 0.05
```

## 1. The comparison

```sh
econocontext compare --fixture ledger --methods react econocontext \
  --context-tokens 1048576 --plan-pressure 0.02 \
  --output-tokens 2048 --max-attempts 60 --retries 3 \
  --deadline 900 --max-cost 2.00 \
  --output docs/runs/$(date +%F)-my-experiment
```

Both arms, identical settings, one variable — `--method`. It prints a cost and
time table as it goes and writes per arm:

- `<method>-walkthrough.txt` — the run narrated component by component
- `<method>-trace.json` — run record, metrics and every event
- `summary.txt` / `summary.json` — the comparison

`ledger` is five modules of roughly 2,250 tokens each with four independent
defects, one per module, and a test whose failure points at a *different* module
than its cause. Hidden verification (`verify.py`) uses different inputs than the
visible tests, so passing cannot be faked.

## 2. The two knobs that decide whether the experiment means anything

**`--plan-pressure`** — the fraction of the context budget at which delegation is
offered. Pressure is `context_tokens × plan_pressure`
(`runtime/agent_loop.py`), and delegation is only a candidate under pressure
(`planning/candidates.py`). At the 0.5 default with a 1M budget the trigger sits
at 524,288 tokens, is never reached, and **both arms run identically** — a null
result that looks like a finding. At 1M, use `0.02`.

**`--observation-tokens`** (default 512) — the floor below which an observation
is not worth an operation. A finding cannot be cheaper than a three-line file.

Before trusting any number, confirm delegation fired:

```sh
grep -c context_pressure docs/runs/*/econocontext-trace.json
```

## 3. Reading the result

```sh
econocontext explain RUN_ID --output walkthrough.txt
econocontext export  RUN_ID --output report.json
```

| What | Where | Means |
|---|---|---|
| `verification: verified` | run record | hidden tests passed — the only pass/fail |
| `known_cost` + `cost_complete` | metrics | spend; `false` means some calls had no usage |
| `wall_seconds` | metrics | start to finish, not the sum of attempt durations |
| `MANAGER answered with a bounded view` | explain | a delegation actually delivered |
| `inline_tokens -> delivered_tokens` | same block | root context spared |
| `MANAGER optimisation abandoned` | explain | a delegation was tried and dropped, with the reason |
| `comparisons[].cost_error` | metrics | predicted minus actual |

**`cost_error` is currently misleading in aggregate.** CONTINUE plans predict a
cost and record `$0.0000` actual, because continuing the root raises no separate
operation and nothing is attributed to it. Only FRESH, REUSE and REPAIR rows are
real comparisons. Filter before averaging.

## 4. Per-call logs

With `ECONOCONTEXT_CALL_LOG` set to a directory, each run writes its own
`run-<unix>-<run_id>.txt` containing, for every call: the full input messages,
the full output, prompt/completion/reasoning/total tokens, the billed split,
cost, and latency with throughput.

One file per run matters. A single shared log appends across runs, and that has
already spoiled a measurement here — growth attributed to one run was the
previous run still sitting in the file.

Logs are gitignored: they contain full prompts and model output.

## 5. What a real run produced

See [runs/2026-09-22-no-context-cap/README.md](runs/2026-09-22-no-context-cap/README.md).

The short version: with no artificial cap, at matched quality, **EconoContext
cost 9.6% more and took 5.8s longer than react.** That is the expected trade on
a task that fits — delegation buys an extra child call plus an integration
exchange and has nothing to save. At a 32,000-token cap the same task was out of
reach for the flat agent entirely.

Cost only means something at matched quality. An arm that is cheaper because it
failed is not cheaper. One run per arm is an existence proof, not a measurement.

## Known rough edges

- A model sometimes answers in prose instead of calling its completion tool,
  which fails the run. It happened to react once in the recorded comparison.
- `apply_patch` messages are the root's own output and no representation choice
  can shrink them — about 21% of context growth in measured runs.
- Context already accumulated cannot be reclaimed. V1 bounds what enters next;
  compaction is not implemented.
