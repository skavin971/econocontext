# Step 9 progress log (overnight run, started 2026-10-11)

One line per finished trial (time UTC | arm | task | repeat | outcome | calls | PFLOPs | validation | retries | repeats)
and every gate decision (`GATE | ...`), written by benchmarks/tblite/run.py --progress, plus notes added by hand.
Gates: 9a -> 9b only if the 4 smoke runs finished, every call validated, the tests pass and the estimate fits the
daily cap. In 9b the run stops cleanly between trials on 3+ invalid calls in a trial, an arm crashing in 2 trials in a
row, Jev falling back on more than half its questions in a trial, the gateway unreachable for 10 minutes, the daily
cap, or (added because the Mac is on battery) the battery at 20% or less while discharging.

- 2026-10-11 02:59:33 UTC | NOTE | 9a smoke test starts (label tc9s)
- 2026-10-11 02:59:33 UTC | START | tc9s: 4 trials (raw, econo+jev, clm-32k, clm-full; 1 tasks; repeats 1-1), 2 workers; 118 of 8000 requests used today
- 2026-10-11 03:00:19 UTC | econo+jev | acl-permissions-inheritance | r1 | pass | 5 calls | 0.143 PFLOPs | valid 5/5 | retries 0 | repeats 0 | Jev 0 asked, 0 fallbacks 
- 2026-10-11 03:00:19 UTC | raw | acl-permissions-inheritance | r1 | pass | 5 calls | 0.154 PFLOPs | valid 5/5 | retries 0 | repeats 0
- 2026-10-11 03:01:07 UTC | clm-32k | acl-permissions-inheritance | r1 | pass | 5 calls | 0.199 PFLOPs | valid 5/5 | retries 0 | repeats 0
- 2026-10-11 03:01:10 UTC | clm-full | acl-permissions-inheritance | r1 | pass | 5 calls | 0.217 PFLOPs | valid 5/5 | retries 0 | repeats 0
- 2026-10-11 03:01:10 UTC | END | tc9s: 4 of 4 trials finished
- 2026-10-11 03:03:00 UTC | GATE | after 9a: PASS (4/4 smoke runs finished and passed, 20/20 calls valid, 87 passed in 13.21s, estimate ~3,263 calls fits 7,862 left today). Starting 9b.
