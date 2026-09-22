# React vs EconoContext, no artificial context cap

2026-09-22 · `google/gemini-3.5-flash` · ledger fixture · one run per arm

The previous comparison capped context at 32,000 tokens, and react died at the
wall. That only showed that a flat agent runs out of room, which is a headroom
result, not a cost result. This run removes the cap so **neither arm can die of
context**, and asks the question that actually matters: when survival is not at
stake, does the delegation decision pay for itself?

## The answer: no, not on a task this size

| Arm | Status | Verified | Cost | Wall | Calls | Input tokens | Cached | Output |
|---|---|---|---|---|---|---|---|---|
| react (attempt 1) | failed | — | $0.0637 | 21.3s | 7 | 30,884 | 19.1% | 2,813 |
| **react (attempt 2)** | **succeeded** | **verified** | **$0.2416** | **56.8s** | 17 | 198,736 | 45.7% | 7,346 |
| **econocontext** | **succeeded** | **verified** | **$0.2650** | **62.5s** | 21 | 214,156 | 45.0% | 8,186 |

**At matched quality — both verified — EconoContext cost 9.6% more and took 5.8
seconds longer.** It made 4 more model calls and sent 15,420 more input tokens.

This is the expected result and it was predicted before the run. Delegation buys
an extra child call plus an integration exchange. On a task that fits
comfortably in the window, there is nothing for it to save, so the trade loses.
`EXPERIMENTS.md` has said so from the beginning.

**What this does not say:** that the harness is not worth it. It locates the
value. At 32,000 tokens react could not finish this task at all and
EconoContext could. At 1,048,576 tokens react finishes and is slightly cheaper.
The value of bounding a context is headroom, and headroom only has a price when
you are near the ceiling. A useful next experiment is a task large enough that
1M is genuinely pressured, which the ledger fixture is not.

## Two things worth correcting

**React's first attempt failed on protocol, not context.** The reason recorded
is *"Model must use structured completion; plain prose is not verified
completion"* — the model wrote an answer instead of calling `complete_task`. It
is a known rough edge, unrelated to the comparison. Attempt 2 used identical
settings and succeeded, so attempt 1 is reported here rather than hidden, but
the comparison uses attempt 2. Note that the retry is a selection: react got two
chances and EconoContext got one.

**The cache-reuse advantage is not real.** Against react's *failed* run,
EconoContext showed 45.0% cached input against 19.1% and that looked like a win
for prefix stability. Against react's *completed* run it is 45.0% against 45.7%
— no advantage at all. The first comparison was an artifact of react's short
run, where a smaller share of the context had been seen before. It is recorded
here because it was nearly reported as a finding.

## The mechanism did fire

Delegation was reached and used, which the previous configuration failed to do:

- Context pressure triggered at **24,643 tokens** (threshold
  `1,048,576 × 0.02 = 20,971`).
- One delegation delivered: **3,006 inline tokens replaced by 1,405 delivered**
  — 53% less entering the root for that observation.
- 8 plans selected, 15 planning passes, 21 assemblies.

So the harness worked as designed. It simply did not have enough context
pressure to be worth the overhead.

## The cost model, which is the part that needs attention

| Plan | View | Predicted | Actual | Error |
|---|---|---|---|---|
| CONTINUE ×7 | — | $0.0202 → $0.1348 | $0.0000 | −$0.02 → −$0.13 |
| FRESH | FOCUSED | $0.0573 | $0.0412 | −$0.0161 |

Only the FRESH row is a real comparison, and there the model **overestimated by
28%**. The seven CONTINUE rows predict a cost and record $0.0000 actual, because
continuing the root raises no separate operation and nothing is attributed to
it. Their `cost_error` is not a measurement of anything, and aggregating it
would make the cost model look far worse than it is. That is a reporting defect
worth fixing before any accuracy claim is made from this field.

## Reproducing

```sh
econocontext compare --fixture ledger --methods react econocontext \
  --context-tokens 1048576 --plan-pressure 0.02 \
  --output-tokens 2048 --max-attempts 60 --retries 3 \
  --deadline 900 --max-cost 2.00 \
  --output docs/runs/$(date +%F)-no-context-cap
```

`--plan-pressure 0.02` is load-bearing. Pressure is computed as
`context_tokens × plan_pressure` (`runtime/agent_loop.py`), and delegation is
only offered under pressure (`planning/candidates.py`). At the 0.5 default with
a 1M budget the trigger sits at 524,288 tokens, is never reached, and
EconoContext degenerates into exactly react — two identical runs and a null
result.

## Files

`*-walkthrough.txt` is the readable narrative of each run, component by
component. `*-trace.json` is the full structured trace: run record, metrics,
every event. Per-call logs with complete request and response bodies are written
to `logs/run-<unix>-<run_id>.txt` and stay local — they contain full prompts and
model output, and are gitignored.

**One run per arm. This is an existence proof, not a measurement.** Total spend
for everything above: $0.57.
