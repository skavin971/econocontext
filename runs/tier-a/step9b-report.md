# Step 9b: the full run (label tc9), 2026-10-11 03:03-07:37 UTC

- **Ran:** 120 of 120 trials (4 arms x 10 tasks x 3 repeats), interleaved by repeat, task and arm, with 2 workers,
  in 4 h 34 min. No gate fired. On AC power throughout, with caffeinate and the lid open, and 0 sleep events in the
  power log. Gateway: 1,785 calls, 0 upstream retries, no refused calls except the 3 window overflows.
- **Completion** (per cell: repeats 1 2 3; OVF = refused over the window, counted as a FAIL; NG = not graded):

| task | raw | econo+jev | clm-32k | clm-full |
|---|---|---|---|---|
| api-endpoint-permission-canonicalizer | pass fail pass | pass pass pass | pass pass pass | pass pass pass |
| sales-data-csv-analysis | pass pass pass | pass pass pass | pass pass pass | pass pass fail |
| acl-permissions-inheritance | pass pass pass | pass pass pass | pass pass pass | pass pass pass |
| maven-slf4j-conflict | pass pass pass | pass pass pass | pass pass pass | pass pass NG |
| chained-forensic-extraction | pass pass pass | pass pass pass | pass pass pass | pass fail pass |
| pandas-etl | pass pass pass | pass pass pass | pass pass pass | pass pass pass |
| malicious-package-forensics | pass pass pass | pass pass pass | pass pass pass | pass pass pass |
| bandit-delayed-feedback | OVF fail fail | fail fail OVF | fail pass pass | pass pass fail |
| anomaly-detection-ranking | pass pass pass | pass pass pass | pass pass pass | pass pass pass |
| scan-linux-persistence-artifacts | pass OVF pass | pass pass NG | pass pass NG | pass pass NG |

- **Per arm:**
  - raw: 25 pass, 3 fail, 2 OVF, 470 calls.
  - econo+jev: 26 pass, 2 fail, 1 OVF, 1 NG, 525 calls.
  - clm-32k: 28 pass, 1 fail, 1 NG, 381 calls.
  - clm-full: 25 pass, 3 fail, 2 NG, 409 calls.
- **Not graded (4):**
  - scan-linux r3, three arms (econo+jev and clm-full: EnvironmentStartTimeoutError, the Docker environment did not start
    in 600 s; clm-32k: RuntimeError, docker compose failed). 0 calls each, so no model work happened. The three started
    within minutes of each other; raw's scan-linux r3 had started first and passed. A Docker build or network race on
    the same task image is the likely cause (Docker's disk is not full: 57 GB used).
  - clm-full maven r3: RuntimeError "Command timed out after 120 seconds". CLM's agent stops the run when a shell command
    exceeds its 120 s command timeout (its default); our agent's limit is 180 s and it reports the timeout to the model
    instead. 20 calls were made and validated, but the run was never graded.
- **Overflows (3, counted as FAIL):** raw bandit r1, raw scan-linux r2, econo+jev bandit r3: a request over Purdue's
  65,536-token window was refused. Neither CLM arm overflowed.
- **Validation:** every answered call validated, per trial (the progress log); re-checked in 9c.
- **Jev:** 1 fallback in the whole run (econo+jev malicious-package r3: 1 of 11 questions, "Jev HTTP 400
  max_tokens_exceeded"): the 2.5-characters-per-token budget is not always enough. Not a gate (well under half).
- **Settings difference found:** CLM's command timeout is 120 s, ours 180 s (it was not in the step's settings table).
