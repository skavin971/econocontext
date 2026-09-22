# Workstream: agents, deep tasks, and cost analysis

What to build and why. `src/agents/README.md` is the how-to for the code; this
is the assignment.

## The finding this workstream exists to fix

From the live run in `runs/2026-09-22-no-context-cap/`:

| | |
|---|---|
| Planning passes | 15 |
| Passes offering exactly **one** candidate | **13** |
| Passes where the optimizer had a real choice | 2 |
| Modes ever generated | CONTINUE ×15, FRESH ×4 |
| REUSE candidates ever generated | **0** |

**The cost model spends 87% of its time pricing a monopoly.** An optimizer
handed one option is not an optimizer, and a cost model that is never asked to
rank anything is untested no matter how carefully it is written.

So the job is not "add logging". Logging already exists — `planner.py:112`
persists every candidate with its estimate and records which was selected. The
job is to **build tasks that manufacture genuine choices**.

## Why the choices are missing, precisely

Read `planning/candidates.py`. One line gates most of it:

```python
delegable = operation.origin == "request" or state.get("pressure_selected", False)
```

FRESH and REUSE both require `delegable`. CONTINUE does not. So **whenever the
root is not under context pressure, exactly one candidate is generated.** That
is the 13.

REUSE has a second gate that makes it unreachable in practice:

```python
reusable = delegable and not state["reuse_uncertain"] and operation.reusable \
           and operation.kind in ("analysis", "research", "diagnosis")
```

`reuse_uncertain` is set permanently the first time the agent runs `test` or
`command` (`runtime/agent_loop.py:264`), because a subprocess may have had
effects the harness cannot see.

**Consequence: in a coding task, the first test run kills REUSE for the rest of
the run.** Every coding task runs tests. That is why the count is zero, and it
is not a bug — it is a soundness rule. It does mean **REUSE can only be
exercised through the research agent**, which raises the research build-out
from a nice-to-have to a prerequisite.

And REPAIR generates nothing because **REPAIR does not exist**: `Mode` has three
members (`contracts.py:33`). It is on the vision page as a designed action.

## What to build

### 1 · Deep tasks, because shallow ones cannot test the hypothesis

The no-context-cap run is the evidence: with no artificial ceiling, EconoContext
cost **9.6% more** than flat ReAct at matched quality. That is the correct
result for a task that fits comfortably — delegation buys an extra child call
plus an integration exchange, and there is nothing for it to save.

So a fixture is only useful if it creates the conditions the system is for.
Concretely, a deep task needs:

- **Enough material that the root cannot hold it all** — many modules, or a
  corpus of real documents, not five files of 2,250 tokens
- **Revisiting** — a file read early, changed, and needed again later. This is
  what makes staleness real rather than theoretical
- **A question asked twice** — the same operation key raised at two different
  moments, which is the only thing that can generate a REUSE candidate
- **Sustained pressure**, not a spike. Delegation has to pay back over
  remaining turns, so the task has to *have* remaining turns

Design each fixture backwards from the mode it is meant to exercise, and say in
its `GOAL.md` which one it targets.

### 2 · The research agent, properly

Today it is `search` + `read` and nothing else. It can find a passage and quote
it. It cannot take a note, trace a claim to its source, or build a report.

- `note` — writes a finding to the store rather than the context. **This is the
  point**: note-taking is what creates context pressure honestly, and pressure
  is what makes delegation reachable at all
- `cite` — binds a claim to an evidence id and a span
- A verifier that checks each claim against the passage it names, instead of
  today's "was `policy.txt` cited and do two numbers appear"

Research tasks run no subprocess, so `reuse_uncertain` stays false — **this is
the only place REUSE can be tested**.

### 3 · Reading what a run cost

```sh
econocontext compare --fixture <f> --methods react econocontext \
  --context-tokens 1048576 --plan-pressure 0.02 --max-cost 1.00 \
  --output docs/runs/$(date +%F)-<name>
```

| Where | What it tells you |
|---|---|
| `summary.txt` | cost and wall time per arm |
| `planning` events in the trace | every candidate, its estimate, and which was selected |
| `selection` events | the plan that was committed |
| `comparisons[].cost_error` | predicted minus actual |
| `logs/run-<unix>-<id>.txt` | every request and response in full, per run |

**Two traps, both real:**

- **`cost_error` is misleading in aggregate.** CONTINUE plans predict
  $0.02–$0.13 and record `$0.0000` actual, because continuing the root raises no
  separate operation and nothing is attributed to it. Filter to FRESH and REUSE
  before averaging, or the cost model looks far worse than it is.
- **`--plan-pressure` is load-bearing.** Pressure is
  `context_tokens × plan_pressure`. At the 0.5 default with a large budget the
  trigger is never reached, delegation never fires, both arms run identically,
  and you get a null result that looks like a finding. Always grep the trace for
  `context_pressure` before believing a number.

### 4 · The measurement to aim at

Not "EconoContext is cheaper". The honest target is **cost at matched quality,
with the plan choices visible** — how many candidates the optimizer saw, which
it took, what it predicted, what it actually paid.

A useful run is one where you can answer *why* it chose what it chose. A run
where it had one option tells you nothing, however good the number looks.

**Record the runs where we lose.** The no-context-cap README does this, and it
is the most credible document in the repo because of it.

## Order of work

1. One research fixture with a question asked twice — the cheapest path to a
   non-zero REUSE count, and therefore the fastest way to make the optimizer
   choose something
2. `note` and `cite` for the research agent, plus a claim-level verifier
3. A deep coding fixture: enough modules that the root cannot hold them, with a
   file that changes mid-run
4. A written comparison in `docs/runs/`, following the format of the existing
   one, including the parts that did not go our way
