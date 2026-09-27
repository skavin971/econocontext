from ..contracts import CandidatePlan, Estimate, FeasibilityError, Limits, Mode


class Optimizer:
    def select(
        self, candidates: list[CandidatePlan], estimates: dict[str, Estimate], limits: Limits
    ) -> CandidatePlan:
        for plan in candidates:
            plan.estimate = estimates[plan.id]
            if plan.estimate.latency > limits.latency:
                plan.rejections.append("estimated latency exceeds operation limit")
            if (
                plan.mode != Mode.REUSE
                and plan.estimate.tokens + limits.output_tokens > limits.context_tokens
            ):
                plan.rejections.append("estimated context exceeds capacity")
            if (
                limits.max_cost is not None
                and plan.estimate.basis in ("USD", "synthetic-USD")
                and plan.estimate.cost > limits.max_cost
            ):
                plan.rejections.append("estimated operation cost exceeds remaining budget")
        eligible = [p for p in candidates if not p.rejections]
        if not eligible:
            raise FeasibilityError("No feasible plan")
        if all(p.estimate.basis == "fixed-fallback" for p in eligible):
            order = {Mode.REUSE: 0, Mode.CONTINUE: 1, Mode.FRESH: 2}
            return min(eligible, key=lambda p: (order[p.mode], p.view or "", p.worker_id or ""))
        return min(
            eligible,
            key=lambda p: (
                p.estimate.cost,
                p.estimate.latency,
                p.mode.value,
                p.view or "",
                p.worker_id or "",
            ),
        )
