# Step 9 progress log (overnight run, started 2026-10-11)

One line per finished trial (time UTC | arm | task | repeat | outcome | calls | PFLOPs | validation | retries | repeats)
and every gate decision (`GATE | ...`), written by benchmarks/tblite/run.py --progress, plus notes added by hand.
Gates: 9a -> 9b only if the 4 smoke runs finished, every call validated, the tests pass and the estimate fits the
daily cap. In 9b the run stops cleanly between trials on 3+ invalid calls in a trial, an arm crashing in 2 trials in a
row, Jev falling back on more than half its questions in a trial, the gateway unreachable for 10 minutes, the daily
cap, or (added because the Mac is on battery) the battery at 20% or less while discharging.

