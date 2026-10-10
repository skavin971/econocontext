"""Worker placement options as loops for the meter: FRESH, RESUME, HANDOFF.

Why it exists: when an agent delegates a task, the work can run in
  FRESH    a new worker: the harness's shared prefix (already cached, if any worker
           started within the cache lifetime) plus the task
  RESUME   an idle worker: its whole history plus the task. Only while its cache is
           warm; a cold worker would write its history again on the first call, which
           cost more than a new worker's whole run in every case observed (wres4)
  HANDOFF  a new worker given a brief: the lines an idle worker already read that the
           task is likely to need, re-read from the workspace at their current version

Each option is one Loop; the meter prices it. The three known errors of the first
placement model are fixed by construction:
  - the resident history is paid on EVERY call of a resumed worker (Loop.cached is in
    every later prompt), not once;
  - FRESH starts from the harness's real cached prefix (`Shape.shared_prefix`,
    measured per harness), not an uncached 9K per call;
  - the call count comes from a forecast per option, not a fixed 5.
What it must never do: decide. decide.py compares; the forecast supplies the calls.
"""

from dataclasses import dataclass

from .meter import Loop


@dataclass(frozen=True)
class Shape:
    """A harness's worker loops, measured from its recorded runs."""
    shared_prefix: float   # tokens a new worker finds cached (system prompt, tools)
    first_tail: float      # a new worker's other first-call tokens, besides its task
    growth: float          # tokens each later call adds
    output: float          # output tokens per call
    resume_tail: float     # a resumed worker's first-call tokens besides the task (its handback)


def fresh(shape: Shape, task: float, calls: float) -> Loop:
    return Loop(cached=shape.shared_prefix, first=shape.first_tail + task, calls=calls,
                growth=shape.growth, output=shape.output)


def resume(shape: Shape, history: float, warm: bool, task: float, calls: float) -> Loop | None:
    """None for a cold worker: never a candidate."""
    if not warm:
        return None
    return Loop(cached=history, first=shape.resume_tail + task, calls=calls,
                growth=shape.growth, output=shape.output)


def handoff(shape: Shape, task: float, brief: float, calls: float) -> Loop:
    return Loop(cached=shape.shared_prefix, first=shape.first_tail + task + brief, calls=calls,
                growth=shape.growth, output=shape.output)


def commit_pending_break_even(after: float, saved: float, r) -> float:
    """COMMIT_PENDING on an explicit cache: replacing a tool result with a pointer saves
    `saved` tokens on every later call, but the `after` tokens from the edit to the end of
    the prompt (all cached until now) are sent again, and `after - saved` of them written.
    The number of calls, counting the next one, after which that pays (no reopen):
        N* = 1 + (w_w*(after - saved) - w_r*after) / (w_r*saved)
    c4's pointer (after 1227, saved 878) needed 4.6 calls; with its 0.3 chance of being
    reopened (0.3 x one more call, about 5.4K NU), about 23. About 14 remained: AS_IS
    was right."""
    return 1 + (r.cache_write_ratio * (after - saved) - r.cache_read_ratio * after) / (
        r.cache_read_ratio * saved)


def break_even_calls_saved(fresh_loop: Loop, other: Loop, cache, rates) -> float:
    """How many fewer calls `other` must take than `fresh_loop` to cost no more
    (0 if it is already cheaper at the same count). Found by bisection on the meter."""
    target = cache.price(fresh_loop, rates)
    if cache.price(other, rates) <= target:
        return 0.0
    lo, hi = 0.0, fresh_loop.calls
    for _ in range(50):
        mid = (lo + hi) / 2
        cheaper = cache.price(Loop(other.cached, other.first, fresh_loop.calls - mid,
                                   other.growth, other.output), rates) <= target
        lo, hi = (lo, mid) if cheaper else (mid, hi)
    return hi
