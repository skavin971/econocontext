# Step 11 validation: our interface at a 32,000-token budget (2026-10-12)

**Setup:**
- Label `tc11v`, one repeat each, one trial at a time, `--budget 32000`, `--interface ours`.
- The interface candidate is commit `8b8cdc3`.
- Gateway settings as in step 10b: max_tokens 2,048, temperature 0.7, top_p 0.95, thinking on.
- CLM on scan-linux was run once in this label, because no clm-32k run existed for that task at the 2,048 cap. CLM on bandit is the 10d run (`tc10s`).
- The 10d runs under the old interface are listed for comparison.

**Command:** `runs/tier-a/scripts/step11_validation_report.py`.

| run | interface | pass | end | agent calls (steps) | summary calls | at the 2,048 cap | cut right after a nudge | PFLOPs | validated |
|---|---|---|---|---|---|---|---|---|---|
| base-32k, bandit | ours | fail | submitted | 21 | 0 | 3 (14%) | 1 of 1 | 2.162 | 21/21 |
| summary-32k, bandit | ours | fail | submitted | 58 | 2 | 4 (7%) | 0 of 1 | 8.408 | 60/60 |
| clm-32k, bandit (10d) | CLM | fail | CLM finished | 16 | 0 | 1 (6%) | 0 of 1 | 2.372 | 16/16 |
| base-32k, scan-linux | ours | pass | submitted | 39 | 0 | 12 (31%) | 8 of 10 | 2.949 | 39/39 |
| clm-32k, scan-linux | CLM | pass | CLM finished | 58 | 0 | 16 (28%) | 2 of 5 | 4.177 | 58/58 |
| raw-32k, bandit (10d) | legacy | fail | submitted | 45 | 0 | 23 (51%) | 10 of 16 | 5.424 | 45/45 |
| summary-32k, bandit (10d) | legacy | fail | max_steps | 64 | 1 | 30 (47%) | 17 of 23 | 8.781 | 65/65 |

**Validation:** every call (178 in tc11v) validated token for token.

## The freeze rule: not met, so the interface is not frozen

Base's share of calls at the cap is 15 of 60 (25%):
- bandit: 3 of 21 (14%), down from 51% under the legacy interface;
- scan-linux: 12 of 39 (31%).

That is above about 15%, so per the rule this stops and reports; nothing was iterated. CLM's own agent is also at 28% on scan-linux at the 2,048 cap. scan-linux pushes long replies for both agents.

## A Summary summary hit the output cap (the 10d rule: stop and report)

Summary on bandit compacted twice.

| | prompt | output | thinking | visible | finish | prompt right after |
|---|---|---|---|---|---|---|
| summary call 30 | 24,682 | 1,530 | 13 | 1,517 | stop | 4,185 |
| summary call 47 | 23,961 | 2,048 | 10 | 2,038 | length | 4,707 |

The second summary is visible text, not thinking. It was cut in the middle of a code block that the model copied into its handoff summary. The runner's gate stopped the label after this trial, as designed.

## Correction to 10e

10e said that all 16 of raw-32k's calls right after a nudge were cut. Counted exactly from the logs, it is 10 of 16. Summary's 17 of 23 stands.

## Budget unit (logs only; `runs/tier-a/step11-budget-unit.out`)

This covers 876 CLM calls, from tc9 (clm-32k and clm-full) and tc10s (clm-32k). It compares CLM's own count (tiktoken o200k over the message text) with the exact Qwen prompt.
- **The ratio:** the Qwen prompt is a median 1.179× CLM's count (p10 1.101, p90 1.264).
- **CLM's real Qwen peak at its 32k setting:** 28,735 tokens (tc9 malicious-package r1). That is under our 29,952 limit, and none of the 446 clm-32k calls exceeded the limit. CLM calibrates its count against the server's `prompt_tokens`, so no change to `context_budget_tokens` is needed.
- **clm-full** (`context_budget_tokens` 57,344) peaked at 50,214.
