"""Fail-open: EconoContext may never be the reason a harness step fails.

Why it exists: EconoContext is an optimization. If any part of it raises, the
harness must proceed exactly as it would without EconoContext.
What it must never do: swallow an exception raised by the host's own work (for
example the delegated task itself). It wraps only EconoContext's deciding.

PLACEHOLDER: the deadline is measured, not enforced. A call that exceeded
`decision_deadline_ms` is logged; the future version preempts it and returns the
host default instead of waiting.
"""

import logging
import time
from collections.abc import Callable
from typing import TypeVar

log = logging.getLogger("econocontext")
T = TypeVar("T")


def guarded(fn: Callable[[], T], default: Callable[[], T],
            deadline_ms: float) -> tuple[T, str | None, float]:
    """Run `fn`; on any exception return `default()`. Returns (result, error, elapsed_ms)."""
    started = time.perf_counter()
    try:
        result, error = fn(), None
    except Exception as exc:  # fail-open by design: any EconoContext failure
        log.warning("econocontext failed open: %s: %s", type(exc).__name__, exc)
        result, error = default(), f"{type(exc).__name__}: {exc}"
    elapsed = (time.perf_counter() - started) * 1000
    # PLACEHOLDER: measured, not enforced; the future version preempts past the deadline.
    if elapsed > deadline_ms:
        log.warning("econocontext decision took %.1f ms (deadline %.0f ms)", elapsed, deadline_ms)
    return result, error, elapsed
