# Step 9a: prep and smoke test (2026-10-11, overnight mode)

- Push of b08b1e3 and 5fd46b1: NOT done. The overnight instruction ("commit locally, don't push anything") came after
  9a item 1 and overrides it. They and every step-9 commit wait for your review.
- caffeinate: benchmarks/tblite/run.py and agents/clm/run.sh start `caffeinate -i -w <own pid>` on macOS, warn when on
  battery, remind that the lid must stay open (the runner also prints the lid state). The runner holds caffeinate
  itself and tells run.sh not to start a second one (ECONO_CAFFEINATED).
- Added gate (not in your list): stop cleanly between trials when the battery is at 20% or less and discharging. At
  02:55 UTC the Mac was on battery (53%); by the smoke test it was on AC power (54%, charging).
- Our agent's step limit: 64 (was 60), with a test.
- One command for all arms: --arm raw,econo+jev,clm-32k,clm-full (CLM arms through agents/clm/run.sh at budgets
  32,000 and 57,344), the same naming, summary.json and run ids; interleaved by repeat, task and arm; the gates.
- measure/report.py: per-task x arm summary (arms.csv); measure/profile.py: arm and repeat columns.
- Tests: 87 passed in 13.21s.

Smoke test (label tc9s, acl-permissions-inheritance, 1 repeat, 2 workers): all 4 passed, every call validated (20/20).

| arm | pass | calls | ΣP | ΣU | ΣG | PFLOPs | context manager |
|---|---|---|---|---|---|---|---|
| raw | pass | 5 | 6,863 | 2,095 | 1,033 | 0.154 | - |
| econo+jev | pass | 5 | 6,703 | 2,063 | 845 | 0.143 | no action (no output >= 1,000 tokens; Jev not asked) |
| clm-32k | pass | 5 | 12,240 | 3,264 | 768 | 0.199 | 0 edits, 0 rollbacks |
| clm-full | pass | 5 | 12,241 | 3,249 | 1,139 | 0.217 | 0 edits, 0 rollbacks |

Settings seen in the logged requests: ours temperature 0.7, no top_p, max_tokens 8,192; CLM temperature 0.7,
top_p 0.95, max_tokens 8,192, chat_template_kwargs enable_thinking true; all reasoning_effort medium (gateway).
CLM's prompts are larger on the same task (ΣP about 12.2k vs 6.8k): its system prompt carries the context-file
instructions.

Estimate (runs/tier-a/step9a-estimate.out): from the step-8 raw profile (206 calls, about 2.1 h of trials per
repeat, sleep removed), EconoContext as raw, CLM 1.3x (32k) and 1.1x (full): about 2,700 calls (3,263 with a 20%
margin) and about 27 h of trials, about 13.6 h with 2 workers. The 20-per-minute limit needs only 2.7 h for those
calls, so it does not bind. Daily cap: 138 of 8,000 used today (UTC 2026-10-11), 7,862 left: the run fits in one day.

Gate after 9a: PASS (4/4 smoke runs finished, 20/20 calls valid, tests pass, the estimate fits the cap). On to 9b.
