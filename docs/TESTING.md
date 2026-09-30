# Testing EconoContext: how to run it

Every step says what it proves, what it costs, and what passing looks like. Run
the steps from the repository root.

## 0. Setup (once)

```sh
python3.12 -m venv .venv                                  # Omnigent needs Python 3.12+
.venv/bin/pip install -e ".[swebench,dev]" -e omnigent_layer  # core, SWE-bench, Omnigent 0.15.0
```

Put the key in `.env` at the repository root. The file is gitignored; never print or commit it.

```
AGENT_PLATFORM_API_KEY=...   # Gemini on Vertex AI: only the gateway reads it
ECONOCONTEXT_BASE_URL=...    # Vertex's OpenAI-compatible endpoint (.../endpoints/openapi)
TYPESAFE_API_KEY=...         # Jev: only for --jev runs (optional)
```

Docker Desktop must be running for SWE-bench. The images are linux/amd64; on an Apple
Silicon Mac they run under emulation (slower, but it works). Each image is about 1 GB.

## 1. Unit tests: free, seconds

```sh
.venv/bin/python -m pytest -q                  # the optimizer alone: 31 passed, 3 skipped
.venv/bin/python -m pytest -q omnigent_layer   # the Omnigent layer: 14 passed
```

- **The optimizer's tests** need no Omnigent, network or Docker. They include the ledger's
  acceptance tests (a real Gemini pilot bill, price periods, incomplete usage) and the
  runtime-span tests (model, tool and dispatch timing, concurrent agents).
- **The layer's tests** cover:
  - the gateway, against a fake upstream: byte-identical pass-through, measurement, arms, caps, and replacing the placeholder key
  - the policy on recorded event shapes
  - the write barrier on a real git repository
  - the container shell

## 2. Planner with and without Jev

See the top of [`tests/test_planner_jev.py`](../tests/test_planner_jev.py). The Jev
module's spec is the docstring of
[`econocontext/planner/jev_planner.py`](../econocontext/planner/jev_planner.py). On
SWE-bench, add `--jev` to `benchmarks/swebench/run.py`; those runs are reported as `econo+jev`.

## 3. Start the two services (paid steps need them)

```sh
.venv/bin/omnigent start --no-open --non-interactive   # Omnigent server + runner host (127.0.0.1:6767)
.venv/bin/python -m omnigent_layer.gateway             # the gateway (127.0.0.1:8787)
```

- **Gateway limits:** it listens on localhost only. It refuses a run's 61st model call,
  and any call once today's input tokens pass 3M. Change the limits with
  `ECONO_MAX_CALLS_PER_RUN` and `ECONO_MAX_INPUT_TOKENS_PER_DAY`.
- **Gateway log:** every call is appended to `logs/gateway/calls.jsonl`, with the path,
  arm, status and usage.
- **Stopping:** `omnigent stop` stops Omnigent.

## 4. One SWE-bench instance: paid, a few minutes

```sh
.venv/bin/python benchmarks/swebench/run.py --label mytest --instance pytest-dev__pytest-5809 --arm baseline
.venv/bin/python benchmarks/swebench/run.py --label mytest --instance pytest-dev__pytest-5809 --arm econo --mode observe
.venv/bin/python harness/report.py --label mytest
```

- **What it proves:** an agent on Omnigent (the `openai-agents` harness with Gemini
  3.6 Flash) fixes a real GitHub issue. Tests run inside the official SWE-bench image,
  every model call is measured at the gateway, and the official harness grades the patch.
- **Arms:** both use `harness/specs/openai_agents.yaml`. The econo arm also attaches the EconoContext
  policy, and the gateway applies `plan_prompt` only for econo runs.
- **Modes:**
  - `observe` logs decisions and changes nothing.
  - `autopilot` applies them, with exact operators only by default.
- **Passing looks like this:** the last line is `{"run_id": ..., "resolved": true}`.
  The report shows `status=done resolved=True`, call and token counts, the cache-read
  share, and the cost in NU and USD.
- **Cost:** priced per call by the ledger (`econocontext/pricing/ledger.py`) from the
  gateway's usage counts. A run is marked incomplete when a required counter was
  missing. Details: [COST_TRACKING.md](COST_TRACKING.md).
- **Other report formats:** add `--format json` or `--format csv` and `--output FILE`.

## 5. Learn from runs: observe, replay, autopilot

Replaces the fixed guesses (turns left `H`, needed-again `p`) with what earlier runs
actually did. Each paid phase needs its own go.

**Once:** `.venv/bin/python harness/setup.py`. This adds the `econo` provider that
workers use (it edits `~/.omnigent/config.yaml` after a backup). After any change to
`omnigent_layer/` code, restart Omnigent: its server keeps the policy module loaded.

| Phase | Command | Paid? |
|---|---|---|
| 1. Observe, then label | `benchmarks/swebench/run.py --label p1 --set mid5 --arm econo --mode observe` then `harness/learn.py label --label p1` | yes, 5 tasks, at most 20 min each |
| 2. Replay | `harness/learn.py replay --label p1` | no |
| 3. Autopilot, learned | `benchmarks/swebench/run.py --label p3 --set mid5 --arm econo --mode autopilot --learned --pointer` then `harness/report.py --label p1 p3` | yes |

(Prefix each command with `.venv/bin/python`.)

**What each produces:**
- **Labels** (`labels` table), from what happened after each decision:
  - `h_actual`: the model calls the agent still made
  - for each tool result, whether it was needed again: re-fetched, or quoted in later output
- **Replay** (per task, and totals):
  - how many decisions would change
  - the NU saved in two ways: in the model's own uncached pricing, and **cache-adjusted** to the run's real cache mix
  - `oracle` uses the true needed-again; `empirical` uses `p_hat` learned from the other tasks
- **Autopilot with `--learned --pointer`:**
  - `H` and `p` come from the labelled runs of the *other* tasks
  - POINTER, COMMIT_PENDING (pointing out old results) and RESUME (continuing a worker) are allowed
  - The report puts p1 and p3 side by side: resolved, calls, tools, uncached/cached tokens, $, and predicted vs actual

Five tasks at temperature 1.0 is a pipeline check, not proof of savings.

## 6. Where results live

| What | Where |
|---|---|
| Everything EconoContext saw and decided | `data/econocontext.sqlite3` (tables: `econocontext/store/schema.sql`) |
| Each model call's path, arm, status and usage | `logs/gateway/calls.jsonl` |
| Work directories and per-run agent specs | `data/work/` |
| Patches sent for grading, and grading output | `data/runs/<label>/` |
| Summary | `harness/report.py --label <label> [<label> ...]` |
| Labels (what happened after each decision) | `labels` table; `harness/learn.py label` |
| The Omnigent session (transcript, tools) | the `session http://127.0.0.1:6767/c/...` link printed by `run.py` |

## Troubleshooting

- **The gateway log shows `unsupported path .../responses`:** the harness is using
  OpenAI's Responses API, and Vertex only serves Chat Completions. Set
  `executor.use_responses: false` in the spec. This works for the root agent only:
  Omnigent 0.15.0 does not pass it to inline sub-agents (see the findings).
- **`run ... is not registered` (400):** runs are registered by `benchmarks/swebench/run.py` before the
  session starts. For a hand-made session, call `omnigent_layer.register_run(...)` first.
- **`429 ... reached 60 model calls`:** the per-run cap stopped the run, as designed.
- **Docker is not running:** start Docker Desktop. Grading never falls back to anything else.
- **Gemini `429 RESOURCE_EXHAUSTED`:** shared capacity is busy. Wait and re-run.
- **No disk space:** run `docker image prune` to remove old SWE-bench images.
- **Cost is incomplete:** inspect the run's `outcomes` rows for the missing provider
  counter. The report keeps the known subtotal and marks the run as not a complete bill.
