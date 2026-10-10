"""Feasibility gates: a plan that fails one is removed, not discounted.

Why it exists: "cheapest feasible, not cheapest". Correctness conditions (a stale
result, a pointer where exact bytes are needed, a broken tool-call pair) are
never traded against price, because the invalid plan is always the cheaper one.
What it must never do: consider cost. Each gate returns (ok, reason).
"""

import json

from ..assembler.zones import pairs_intact, zoned_order
from ..types import Candidate, PlanContext

Gate = tuple[bool, str]


def allowlist(c: Candidate, ctx: PlanContext) -> Gate:
    ok = c.is_host_default or ctx.allowlist.get(c.name, False)
    return ok, "" if ok else "operator not enabled in the allowlist"


def quality(c: Candidate, ctx: PlanContext) -> Gate:
    ok = c.is_host_default or c.quality_risk <= ctx.constraints.max_quality_risk
    return ok, "" if ok else (f"quality_risk {c.quality_risk} > max_quality_risk "
                              f"{ctx.constraints.max_quality_risk}")


def fidelity(c: Candidate, ctx: PlanContext) -> Gate:
    bad = c.name in ("POINTER", "COMMIT_PENDING") and c.payload.get("needs_exact_bytes")
    return not bad, "exact bytes are needed; a pointer would not do" if bad else ""


def version(c: Candidate, ctx: PlanContext) -> Gate:
    read_set = c.payload.get("read_set")
    if read_set is None:
        return True, ""
    if isinstance(read_set, str):
        read_set = json.loads(read_set)
    stale = [src for src, ver in read_set.items() if ctx.current_versions.get(src, "0") != ver]
    return not stale, f"sources changed since it was stored: {sorted(stale)[:3]}" if stale else ""


def side_effects(c: Candidate, ctx: PlanContext) -> Gate:
    bad = c.payload.get("from_store") and c.payload.get("side_effect")
    return not bad, "the stored result came from a side-effecting call" if bad else ""


def window(c: Candidate, ctx: PlanContext) -> Gate:
    limit, size = ctx.window_max_tokens, c.payload.get("send_tokens")
    ok = limit is None or size is None or size <= limit
    return ok, "" if ok else f"request of {size} tokens exceeds the window of {limit}"


def pairing(c: Candidate, ctx: PlanContext) -> Gate:
    # Only ZONED reorders; the host's own order (AS_IS) is the host's responsibility,
    # and RETRIEVE_FROM_STORE / COMMIT_PENDING never move a message.
    ok = c.name != "ZONED" or pairs_intact(zoned_order(ctx.window))
    return ok, "" if ok else "reordering would separate a tool call from its results"


GATES = (allowlist, quality, fidelity, version, side_effects, window, pairing)


def check(c: Candidate, ctx: PlanContext) -> Gate:
    for gate in GATES:
        ok, reason = gate(c, ctx)
        if not ok:
            return False, f"{gate.__name__}: {reason}"
    return True, ""
