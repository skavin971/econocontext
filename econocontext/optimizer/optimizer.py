"""Selection under the user's constraints: gates first, then price, then choose.

Why it exists: one `select(candidates, context, constraints) -> Decision`
interface that any smarter selector can replace without touching callers.
What it must never do: return nothing. The host default is always a candidate,
and it is returned (with every reason recorded) when nothing else qualifies.

Objectives:
- cost:     minimum NU, subject to max_latency_ms
- latency:  minimum ms, subject to max_cost_nu
- balanced: minimum NU + latency_weight x ms (an explicit config knob)
Ties: equal NU -> lower predicted latency -> the host default.
"""

import uuid
from datetime import datetime, timezone

from ..pricing.cost_model import price
from ..types import (Candidate, Constraints, CostBreakdown, Decision, Objective, PlanContext,
                     Rejection)
from .gates import check


def _key(c: Candidate, cost: CostBreakdown, k: Constraints):
    default_last = 0 if c.is_host_default else 1  # prefer the host default on a full tie
    nu, ms = round(cost.total, 6), round(cost.latency_ms, 3)
    if k.objective == Objective.LATENCY:
        return (ms, nu, default_last)
    if k.objective == Objective.BALANCED:
        return (round(nu + (k.latency_weight or 0) * ms, 6), ms, default_last)
    return (nu, ms, default_last)


def select(candidates: list[Candidate], context: PlanContext, constraints: Constraints,
           cfg: dict) -> Decision:
    rejected: list[Rejection] = []
    costs: dict[str, CostBreakdown] = {}
    feasible: list[Candidate] = []
    for c in candidates:
        costs[c.name] = price(c, context, cfg)
        ok, reason = check(c, context)
        if not ok:
            rejected.append(Rejection(c.name, reason))
            continue
        cost = costs[c.name]
        if constraints.max_latency_ms is not None and cost.latency_ms > constraints.max_latency_ms:
            rejected.append(Rejection(c.name, f"latency {cost.latency_ms:.0f} ms > "
                                              f"max_latency_ms {constraints.max_latency_ms}"))
            continue
        if constraints.max_cost_nu is not None and cost.total > constraints.max_cost_nu:
            rejected.append(Rejection(c.name, f"cost {cost.total:.0f} NU > max_cost_nu "
                                              f"{constraints.max_cost_nu}"))
            continue
        feasible.append(c)
    default = next(c for c in candidates if c.is_host_default)
    if feasible:
        chosen = min(feasible, key=lambda c: _key(c, costs[c.name], constraints))
    else:
        chosen = default  # nothing qualified: the host proceeds exactly as it would alone
    for c in feasible:
        if c is not chosen:
            rejected.append(Rejection(c.name, f"not the best under objective "
                                              f"{constraints.objective.value}: "
                                              f"{costs[c.name].total:.1f} NU / "
                                              f"{costs[c.name].latency_ms:.0f} ms vs "
                                              f"{costs[chosen.name].total:.1f} NU / "
                                              f"{costs[chosen.name].latency_ms:.0f} ms"))
    return Decision(id=uuid.uuid4().hex, intercept=context.intercept, chosen=chosen,
                    cost=costs[chosen.name], rejected=rejected,
                    feasible=[c.name for c in feasible], candidate_costs=costs,
                    created_at=datetime.now(timezone.utc).isoformat())
