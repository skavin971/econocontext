# EconoContext MVP

EconoContext selects a worker, evidence view, and execution mode for bounded agent operations. One deterministic Python planner shares a local FastAPI process with a root agent, at most two reusable child sessions, SQLite, and immutable artifact files.

**Offline demonstrations are synthetic protocol tests, not evidence of model quality or cost savings.** No model download, credential, or paid endpoint is required.

## Install and run

From the repository root, with Python 3.11 or newer:

```sh
cd mvp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
```

A task is a prompt, your data, and constraints:

```sh
econocontext run --prompt "Fix the failing parser test" --data ./myrepo --max-cost 1.00
```

Your directory is snapshot-copied into the run workspace, so a failed run never
touches your files. Such tasks finish `unverified`: the harness has no grader
for your work. Add `--commit SHA` when `--data` is a git repository.

Built-in fixtures need no data and are verified, so the harness can be exercised
without credentials or spend:

```sh
econocontext run --fixture ledger --method econocontext
econocontext run --fixture ledger --method react        # the baseline
econocontext run --fixture corpus --adapter research
```

`--step` walks one run component by component, pausing at each handoff.
Afterwards:

```sh
econocontext explain RUN_ID --output walkthrough.txt
econocontext export RUN_ID --output report.json
econocontext profiles CALIBRATION_RUN_ID --output profiles.json
econocontext reconstruct MANIFEST_HASH --output request.json
```

As a library:

```python
from econocontext import Harness, Limits

harness = await Harness.open()
run = await harness.submit(
    prompt="Fix the failing parser test", data="./myrepo", limits=Limits(max_cost=1.00)
)
```

## Layout

`src/econocontext/` is the harness: planning, costing, assembly, memory,
measurement. It carries no domain knowledge and imports nothing from `agents`.
`src/agents/` holds the domain agents built on it — tools, verification, and the
fixtures the test suite runs on. The dependency runs one way, so a different
agent is a new module rather than a change to the harness.

## API

```sh
uvicorn econocontext.api:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
curl -s http://127.0.0.1:8000/health
curl -s -X POST http://127.0.0.1:8000/runs -H 'Content-Type: application/json' \
  -d '{"task":{"adapter":"coding"},"method":"econocontext","idempotency_key":"my-first-run"}'
curl -s http://127.0.0.1:8000/runs/RUN_ID
curl -s 'http://127.0.0.1:8000/runs/RUN_ID/trace?after=0&limit=100'
curl -s http://127.0.0.1:8000/runs/RUN_ID/metrics
curl -s -X POST http://127.0.0.1:8000/runs/RUN_ID/cancel
```

Run creation returns HTTP 202 and does not wait for execution. Repeating an identical idempotency key returns the same run; changing its request returns 409. Missing runs return 404, invalid requests 422. `/docs` provides interactive API documentation. Trace pages return a `next_cursor`.

The default queue runs one job at a time. Startup marks interrupted running jobs without replaying effects; queued jobs can start. Cancellation stops further scheduling, terminates supported local subprocesses, and records uncertain remote usage. The API is a trusted localhost control interface, not an authenticated multi-tenant service.

## Configuration

See `.env.example`; export variables in the shell before starting the CLI/API. Backend, model, endpoint, pricing and frozen profiles are process-level configuration shared by root and children. Per-run `limits` are validated in `POST /runs`.

| Setting | Default |
| --- | --- |
| `ECONOCONTEXT_DATA_DIR` | `./data` |
| `ECONOCONTEXT_BACKEND` | `scripted` |
| `ECONOCONTEXT_BASE_URL` | `https://api.openai.com/v1` |
| `ECONOCONTEXT_MODEL` | `scripted-v1` |
| `ECONOCONTEXT_CREDENTIAL_ENV` | `OPENAI_API_KEY` |
| `ECONOCONTEXT_OUTPUT_PARAMETER` | `max_completion_tokens` |
| `ECONOCONTEXT_TIMEOUT` | 30 seconds |
| `ECONOCONTEXT_LIVE_CONTEXT_TOKENS` | Required in live mode |
| `ECONOCONTEXT_PRICING` | JSON rates; absent means unknown live cost |
| `ECONOCONTEXT_PROFILE_PATH` | Optional frozen profile JSON |

Demo limits: 32 model attempts, 1,024 output tokens per call, 16,384 context tokens, 2,048 focused / 4,096 broader evidence tokens, two children, one retry, 30-second tools, 300-second deadline, 120-second estimated operation latency. A UTF-8 size heuristic applies a 20% token safety margin. These values are modest demo settings, not assertions about any live model.

Live mode requires an explicit model ID and capacity. Supply actual prices to enable monetary optimization. Without comparable prices, selection uses a documented fixed policy and cost remains incomplete. No secret values or authorization headers enter request artifacts.

## Tests and packaging

```sh
ruff check .
ruff format --check .
pytest -q
python -m build --no-isolation
docker compose up --build
```

Compose publishes the API on localhost and mounts `/data` as a named local volume. Use one application process; do not place SQLite WAL storage on network filesystems. No model is bundled.

## Outputs and limitations

`data/state.sqlite3` contains indexed record references, bindings, messages and events; `data/artifacts/` contains immutable hash-addressed payloads. `data/workspaces/RUN_ID/` contains the staged task files. The run's `final_artifact` points to a JSONL patch record with `instance_id`, `model_name_or_path`, and `model_patch`. Exact model requests reconstruct from assembly manifests using the CLI.

The coding adapter supports trusted local code, root-only unique-span patches, and bounded commands. It is **not an OS sandbox for hostile code**. External repository/corpus tasks remain `unverified` until an external evaluator checks them. Official SWE-bench execution, ACM and Context-Folding baselines, live web research, `REPAIR`, recursive delegation, RL and distributed execution are deferred.

Start with [EXPERIMENTS.md](EXPERIMENTS.md) to run it and read the results. See [architecture](docs/architecture.md), [measurement](docs/measurement.md), [decisions](docs/decisions.md), and [benchmark protocol](docs/benchmark_protocol.md). Validation details are in `docs/validation.md`.
