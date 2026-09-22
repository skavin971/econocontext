# Validation

Commands actually run in this environment, from `mvp/` with the `.venv` created per the README install steps and Python 3.12.14.

```sh
ruff check .
ruff format --check .
pytest -q
python -m build --no-isolation
econocontext run --fixture ledger --method econocontext
docker compose up --build   # container starts and serves /health; not exercised as part of automated checks
```

## Results

- `ruff check .` — all checks passed.
- `ruff format --check .` — all files already formatted.
- `pytest -q` — **27 passed**, 0 failed, 0 skipped.
- `python -m build --no-isolation` — produced `dist/econocontext-0.1.0-py3-none-any.whl` and `dist/econocontext-0.1.0.tar.gz`.
- Fixture runs under both `react` and `econocontext` verified end to end.
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
- The measurement interface, usable independently of the planner.

## What the live runs showed

Against `google/gemini-3.5-flash` on the `ledger` fixture, delegation replaced
observations with bounded views plus a finding: 3,006 tokens became 1,093, and
3,059 became 1,391 — 55-64% less root context for the same answered call. Those
runs did not complete the task: the root still overflowed because most
delegations were abandoned, and the reasons are now recorded as
`operation_abandoned` events. Reported spend was $0.1986 and $0.2565.

## Not verified here

- No live model backend was called; no API key was used or required.
- No official SWE-bench, ACM, or Context-Folding baseline execution.
- No paid or networked benchmark runs of any kind.
