# Validation

Commands actually run in this environment, from `mvp/` with the `.venv` created per the README install steps and Python 3.12.14.

```sh
ruff check .
ruff format --check .
pytest -q
python -m build --no-isolation
econocontext demo
docker compose up --build   # container starts and serves /health; not exercised as part of automated checks
```

## Results

- `ruff check .` — all checks passed.
- `ruff format --check .` — 28 files already formatted.
- `pytest -q` — **25 passed**, 0 failed, 0 skipped.
- `python -m build --no-isolation` — produced `dist/econocontext-0.1.0-py3-none-any.whl` and `dist/econocontext-0.1.0.tar.gz`.
- `econocontext demo` — ran coding and research fixtures under both `react` and `econocontext`, all four verified. Full output saved at `examples/demo_report.json`.
- Docker is installed and running locally; the image builds and the API starts under `docker compose up --build`, but no additional checks beyond a running `/health` were exercised through the container.

## What the test suite covers

`tests/test_mvp.py` exercises, among other things:

- Both domain adapters (`coding`, `research`) under both methods (`react`, `econocontext`), verified end to end.
- The full API surface — health, run creation, idempotency (same key returns the same run; changed request returns 409), status, paginated trace, metrics, cancellation, 404 on a missing run, 422 on an invalid request.
- Inline root continuation, prior-worker reuse, and rejection of stale continuation.
- Profile-driven selection of a broader view driving actual execution.
- Final-assembly reselection when the selected plan becomes infeasible right before submission.
- Unique-attempt accounting across retries and replayed completion events, including unknown usage and disjoint cache accounting.
- Budget exhaustion with no feasible candidate remaining.
- Cancellation, run deadlines, and tool timeouts, including that original (untruncated) tool output is retained.
- Startup recovery: a previously running job is marked interrupted without replaying its effects; a graceful shutdown does the same for an active run.
- Cross-run isolation — no reuse of results or workers across separate runs.
- Parent integration headroom rejecting delegation when the parent lacks capacity.
- The live OpenAI-compatible backend's request/response contract, checked against a mock HTTP transport (no network call, no paid model).
- The external-run measurement fixture (`examples/external_runner.py`'s interface), independent of the planner.

## What `examples/demo_report.json` shows

Four runs from an actual `econocontext demo` execution: coding/`react`, coding/`econocontext`, research/`react`, research/`econocontext`. All are `status: succeeded` and `verification: verified`. The two `econocontext`-method runs additionally report per-operation `comparisons` — predicted versus actual cost, tokens, and latency for the selected `FRESH`/`FOCUSED` and `REUSE` candidates, with signed `cost_error`/`latency_error`. These are synthetic scripted-backend numbers (`"synthetic": true`); they demonstrate the measurement and prediction plumbing works, not real model quality or cost savings.

## Not verified here

- No live model backend was called; no API key was used or required.
- No official SWE-bench, ACM, or Context-Folding baseline execution.
- No paid or networked benchmark runs of any kind.
