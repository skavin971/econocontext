import time

from .candidates import CandidateGenerator
from .contracts import FeasibilityError, Mode, Worker, digest
from .cost_model import CostModel
from .optimizer import Optimizer


class Planner:
    def __init__(self, memory, config, assembler):
        self.memory, self.assembler = memory, assembler
        self.generator, self.cost, self.optimizer = (
            CandidateGenerator(),
            CostModel(config),
            Optimizer(),
        )

    async def plan(self, operation, state, excluded=None, trigger="operation-boundary"):
        start = time.monotonic()
        matches = await self.memory.find_candidates(operation, state)
        candidates = self.generator.generate(operation, state, matches)
        state["candidate_tokens"] = {}
        state["candidate_prefixes"] = {}
        state["prefix_tokens"] = {}
        parent_history = await self.memory.history(state["worker"].id)
        # A pending operation request receives a bounded tool response before integration.
        parent_messages = [
            dict(role="system", content=state["adapter"].prompt(state["worker"], state["method"]))
        ] + parent_history
        state["parent_tokens"] = self.cost.render_estimate(
            parent_messages,
            state["adapter"].tools(state["worker"], state["method"]),
            state["limits"],
        )
        for candidate in candidates:
            if candidate.label() in (excluded or set()):
                candidate.rejections.append(
                    "previous final assembly or binding check rejected this alternative"
                )
            if candidate.mode == Mode.REUSE:
                state["candidate_tokens"][candidate.id] = 0
            else:
                worker = (
                    state["worker"]
                    if candidate.worker_id == state["worker"].id
                    else (
                        Worker(**await self.memory.get(candidate.worker_id))
                        if candidate.worker_id
                        else Worker(
                            run_id=operation.run_id,
                            role="child",
                            scope=operation.scope,
                            fingerprint=state["fingerprint"],
                        )
                    )
                )
                history = await self.memory.history(worker.id) if candidate.worker_id else []
                # Metadata-only evidence sizing uses the same serialization/token helper.
                messages = [
                    dict(role="system", content=state["adapter"].prompt(worker, state["method"]))
                ] + history
                messages.append(dict(role="user", content=operation.goal))
                state["candidate_prefixes"][candidate.id] = digest(
                    [messages[:1], state["adapter"].tools(worker, state["method"])]
                )
                state["prefix_tokens"][candidate.id] = self.cost.render_estimate(
                    messages[:1], state["adapter"].tools(worker, state["method"]), state["limits"]
                )
                count = self.cost.render_estimate(
                    messages, state["adapter"].tools(worker, state["method"]), state["limits"]
                )
                count += sum(matches["evidence"][r].tokens for r in candidate.evidence)
                state["candidate_tokens"][candidate.id] = count
                if any(
                    state["versions"].get(matches["evidence"][r].source)
                    != matches["evidence"][r].version
                    for r in candidate.evidence
                ):
                    candidate.rejections.append("selected evidence version is stale")
            delegated = candidate.mode != Mode.CONTINUE or candidate.worker_id != state["worker"].id
            if (
                delegated
                and state["parent_tokens"]
                + operation.result_tokens
                + state["limits"].output_tokens
                + 128
                > state["limits"].context_tokens
            ):
                candidate.rejections.append("insufficient parent integration headroom")
        for candidate in candidates:
            if candidate.label() in state.get("exact_tokens", {}):
                state["candidate_tokens"][candidate.id] = state["exact_tokens"][candidate.label()]
        estimates = {c.id: self.cost.estimate(operation, state, c) for c in candidates}
        try:
            selected = self.optimizer.select(
                candidates, estimates, state.get("planning_limits", state["limits"])
            )
        except FeasibilityError:
            selected = None
        await self.memory.event(
            operation.run_id,
            "planning",
            dict(
                operation_id=operation.id,
                trigger=trigger,
                candidates=[c.model_dump(mode="json") for c in candidates],
                selected=selected.id if selected else None,
                duration=time.monotonic() - start,
            ),
        )
        if selected is None:
            raise FeasibilityError("No feasible plan; inspect candidate rejection reasons")
        return selected
