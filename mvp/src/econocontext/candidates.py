from .contracts import CandidatePlan, Mode, Operation
from .state import CandidateMatches, ExecutionState


class CandidateGenerator:
    def generate(
        self, operation: Operation, state: ExecutionState, matches: CandidateMatches
    ) -> list[CandidatePlan]:
        limits = state["limits"]

        def bounded(ids, budget, initial):
            selected = list(initial)
            count = sum(matches["evidence"][r].tokens for r in selected)
            for ref in ids:
                if ref in selected or ref not in matches["evidence"]:
                    continue
                size = matches["evidence"][ref].tokens
                if count + size <= budget:
                    selected.append(ref)
                    count += size
            return selected

        focused = bounded(matches["direct"], limits.focused_tokens, matches["required"])
        broader = bounded(matches["related"], limits.broader_tokens, focused)
        revision = matches["revision"]
        common = dict(operation_id=operation.id, state_revision=revision)
        plans = []
        reusable = (
            not state.get("reuse_uncertain", False)
            and operation.reusable
            and operation.kind in ("analysis", "research", "diagnosis")
        )
        for result in sorted(matches["results"], key=lambda r: r.id):
            if (
                reusable
                and result.reusable
                and result.fingerprint == state["fingerprint"]
                and all(state["versions"].get(k) == v for k, v in result.requirements.items())
                and result.verification != "failed"
            ):
                plans.append(CandidatePlan(**common, mode=Mode.REUSE, result_id=result.id))
                break

        def eligible(worker):
            return (
                worker.status != "retired"
                and worker.fingerprint == state["fingerprint"]
                and all(state["versions"].get(k) == v for k, v in worker.bindings.items())
            )

        current = state["worker"]
        if eligible(current):
            plans.append(
                CandidatePlan(**common, mode=Mode.CONTINUE, worker_id=current.id, evidence=focused)
            )
        prior = sorted(
            [
                w
                for w in matches["workers"]
                if w.id != current.id
                and w.role == "child"
                and w.status == "idle"
                and eligible(w)
                and w.scope == operation.scope
            ],
            key=lambda w: w.id,
        )
        if prior:
            plans.append(
                CandidatePlan(**common, mode=Mode.CONTINUE, worker_id=prior[0].id, evidence=focused)
            )
        # A harness-raised observation only justifies a new child once the root is
        # actually under pressure; a model-requested operation always may.
        delegable = operation.origin == "request" or state.get("pressure", False)
        if limits.max_children and delegable:
            plans.append(CandidatePlan(**common, mode=Mode.FRESH, view="FOCUSED", evidence=focused))
            if broader != focused:
                plans.append(
                    CandidatePlan(**common, mode=Mode.FRESH, view="BROADER", evidence=broader)
                )
        return plans[:5]
