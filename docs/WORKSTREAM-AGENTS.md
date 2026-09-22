# Workstream: agents and cost measurement

## Where we are

The harness plans and prices work. The agents we point it at are too small to
make that planning mean anything.

From the last live run: of 15 planning passes, **13 offered the planner exactly
one candidate**. REUSE has never been generated once, and REPAIR does not exist
yet. At a full context window, EconoContext cost 9.6% more than a flat agent at
matched quality — the right answer for a task that fits comfortably, and a sign
that the task is not testing the thing we built.

Two mechanisms are worth knowing before designing anything, because they shape
what is reachable: delegation is only offered once the root passes a fraction of
its context budget, and `reuse_uncertain` is set permanently the first time an
agent runs a subprocess.

## What we need

- **Tasks long enough to create real context pressure.** The delegation path
  only engages under pressure, and nothing we currently run gets there. This is
  the blocker for testing the central claim.

- **A problem designed backwards from the mechanisms we want to exercise.**
  Reverse-engineer it: what would a task have to look like for REUSE to be
  worth offering, or for a worker's knowledge to go stale mid-run? Half the
  action space has never run against a live model.

- **Cost measured on every run**, predicted against actual, so we can say
  whether the cost model is right rather than whether it is fast.

- **Every candidate plan the planner considered, not just the one it chose.**
  We need to see the options it had and why it ranked them that way. The data is
  already recorded; the problem is that there is usually nothing in it.

- **A research agent with actual capability.** It can find a passage and quote
  it, and nothing else — no notes, no citations, no report. It is also the only
  place REUSE can ever be exercised, since research tasks run no subprocess.

- **Results written up honestly, including the runs we lose.** Cost only means
  something at matched quality, and a run that was cheaper because it failed is
  not cheaper.

## Where things are

`src/agents/README.md` for how the code works. `docs/EXPERIMENTS.md` for running
and reading a comparison. `docs/runs/2026-09-22-no-context-cap/README.md` for
the run above, written up the way these should be.
