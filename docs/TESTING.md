# Testing EconoContext: how to run it

Every step says what it proves, what it costs, and what passing looks like. Run
the steps from the repository root.

## 0. Setup (once)

```sh
python3.12 -m venv .venv                     # Python 3.11+ works
.venv/bin/pip install -e ".[host,dev]"       # exact versions are pinned in pyproject.toml
```

Put the keys in `.env` at the repository root. The file is gitignored; never print or commit it.

```
AGENT_PLATFORM_API_KEY=...   # Gemini on Vertex AI: needed for any agent run
TYPESAFE_API_KEY=...         # Jev: only for --jev runs (optional)
```

Docker Desktop must be running for the SWE-bench steps. The images are linux/amd64;
on an Apple Silicon Mac they run under emulation. That is slower, but it works. Each
image is about 1 GB, pulled on first use.

## 1. Unit tests: free, about 3 seconds

```sh
.venv/bin/python -m pytest -q
```

This proves the core logic works on plain data, with no LLM, no network and no Docker.

Passing looks like this:

```
all unit tests pass
```

The cost acceptance tests cover the Gemini pilot bill, price periods, and
incomplete usage. Runtime span tests cover model/tool/dispatch timing and
concurrent agent activity.

## 2. Planner with and without Jev

See the top of [`tests/test_planner_jev.py`](../tests/test_planner_jev.py): it
explains both tracks and gives every command. The spec for the Jev module (inputs,
contract, API, constraints) is the docstring of
[`econocontext/planner/jev_planner.py`](../econocontext/planner/jev_planner.py).

## 3. One SWE-bench instance: paid, about $0.10 and a few minutes

```sh
./scripts/run_baseline.sh --label mytest --set dev                   # stock Deep Agents
./scripts/run_econo.sh    --label mytest --set dev --mode observe    # + EconoContext (logs only)
.venv/bin/python scripts/report.py --label mytest
```

- **What it proves:** a real coding agent (Deep Agents with Gemini 3.6 Flash) fixes a real GitHub issue inside the official SWE-bench Docker image. Every model call is measured, and the official SWE-bench harness grades the patch.
- **Instance:** `--set dev` is `pytest-dev__pytest-5809`.
- **Arms:** the two arms differ only in whether EconoContext's decision middleware is installed.
- **Modes:**
  - `observe` logs decisions but changes nothing the agent sees.
  - `autopilot` applies them, but only exact operators by default, meaning byte-identical reuse.
- **Passing looks like this:** each run ends with `resolved: [...]`. The report shows `status=completed resolved=True`, with call and token counts.
- **Cost:** the report shows known NU/USD totals and marks runs incomplete when
  a required provider counter was unavailable.

## 4. The five-instance pipeline check: paid, about $4 and 80 minutes

```sh
./scripts/run_baseline.sh --label check6 --set comparison
./scripts/run_econo.sh    --label check6 --set comparison --mode autopilot
.venv/bin/python scripts/report.py --label check6
```

This is a pipeline check: the whole path runs and is measured. It is not a savings
comparison. Five instances at temperature 1.0 vary from run to run more than any
effect EconoContext has so far.

**Last result: `check5`, 2026-09-27.** Gemini 3.6 Flash, promo prices, costed by the
ledger before it was cleared for rebuilding:

| Instance | Baseline | Econo (autopilot) |
|---|---|---|
| psf__requests-2317 | resolved, 38 calls, $0.177 | resolved, 45 calls, $0.295 |
| pallets__flask-5014 | resolved, 32 calls, $0.149 | resolved, 48 calls, $0.248 |
| pylint-dev__pylint-4970 | resolved, 63 calls, $0.554 | **not resolved**, 87 calls, $0.617 |
| pytest-dev__pytest-7432 | resolved, 28 calls, $0.152 | resolved, 39 calls, $0.193 |
| sphinx-doc__sphinx-8721 | resolved, 94 calls, $0.847 | resolved, 63 calls, $0.509 |
| **Total** | **5/5, 255 calls, $1.879** | **4/5, 282 calls, $1.862** |

- EconoContext applied 6 decisions, all answering a repeated tool call from the store with byte-identical output. Every other decision was the host's default. It never failed open.
- **Known issue:** the baseline sphinx patch was 625 KB, because `sandbox.patch()` (`git add -A`) also picked up Sphinx build output the agent created in `_build/`. It was still graded as resolved. The fix is to skip build and cache folders when making the patch.

## 5. Where results live

| What | Where |
|---|---|
| Everything EconoContext saw and decided | `data/econocontext.sqlite3` (gitignored). The tables are described in `econocontext/store/schema.sql` |
| Patches sent for grading | `data/runs/<label>/<arm>.jsonl` |
| Official grading output | `data/runs/<label>/` |
| Side-by-side summary | `scripts/report.py --label <label>` |

## Troubleshooting

- **`Docker is not running`:** start Docker Desktop. SWE-bench steps never fall back to anything else.
- **Gemini `429 RESOURCE_EXHAUSTED`:** shared capacity is busy. Wait a few minutes and re-run; the step limit still applies.
- **No disk space:** run `docker image prune` to remove old SWE-bench images.
- **Cost is incomplete:** inspect the outcome rows for the missing provider
  counters; the report preserves the known subtotal and marks the run unsafe to
  treat as a complete bill.
- **Budgets:** completed usage is priced before the next model call. If a prior
  call has incomplete usage, the next call is refused because the budget cannot
  be verified; the step limit remains the fallback safety cap.
