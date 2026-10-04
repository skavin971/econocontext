# Recall-required probe (design only, not built)

**Question:** when a detail the task needs sits only in the cut middle of an output that cannot be regenerated, does an EconoCLM arm fetch it with `econo get`, and does that change the pass rate?

**Arms:** Raw CLM vs v1.1 ("facts only"), 5 runs each per task, 3 tasks: 30 trials. Same model, gateway, `max_tokens 8192` and `--workers 2` as the main runs.

## Shared mechanics (all 3 tasks)

- The task ships a one-shot generator, `/app/run_once`. On its first run it draws fresh random values and prints a long output (about 40–60K characters). The needed detail is placed near the middle of that output.
- CLM keeps the first and last 5,000 characters of any output over 10,000 and drops the rest. So the needed detail is never in the model's context.
- After the first run, `run_once` refuses to run again (`already run`). It also never writes its output to disk unless the agent redirects it.
- It stores only `sha256` hashes of the needed values, in `/app/.expected`. The verifier compares hashes, so the answer cannot be read off the disk.
- **Legitimate ways to succeed:**
  - Raw: redirect the first run to a file (`./run_once > out.txt`), then grep it.
  - v1.1: either that, or `econo get N A-B` on the saved output (the exact-range tag names the lines).
  - The probe records which way each trial used.
- **Oracle solution:** `./run_once > out.txt` plus `grep`, so the Harbor health check can confirm each task is solvable.

## The tasks

1. **`recall-random-key`**
   - `run_once` prints a 50K-character build log with one line in the middle: `issued deploy key: <32 hex chars>`.
   - Task: write the key to `/app/answer.txt`.
2. **`recall-config-value`**
   - `run_once` prints a 45K-character configuration dump. In the middle sits `db.pool.max = <random int 37-913>` and `db.pool.timeout_ms = <random int>`.
   - Task: write `/app/pool.json` with those two values.
3. **`recall-failing-tests`**
   - `run_once` prints a 60K-character test-run log with 400 tests. Three randomly chosen ones in the middle are `FAILED`.
   - Task: list the three failing test names in `/app/failed.txt`, any order.

## Measures (per arm)

- Pass rate.
- How each trial got the detail: `econo get` on the cut output, a redirect to a file before the first run, or neither (a guess, or failed).
- `econo` use (the engagement rule: at least 3 of 15 v1.1 trials), and cost.

## Cost estimate

- **Per trial:** about $0.04–0.08. These are small tasks of about 8–15 calls, similar to Gate 4's `acl` ($0.045) and `pandas` ($0.080); the one large output adds about 10K prompt tokens per call until it is edited out.
- **30 trials:** about $1.2–2.4.
- **One pilot per arm:** about $0.2.
- **Total: about $1.5–2.6**, and about 40 minutes of wall time at 2 workers.
- **Build:** 3 Harbor task folders (Dockerfile, instruction, `run_once`, oracle, verifier) plus an oracle health check. Half a day; no model cost.
