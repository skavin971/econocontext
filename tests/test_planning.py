"""Planning: candidate generation, pricing, selection, delegation.

Owned by the planning workstream -- planning/ and assembler.py."""

import pytest
from conftest import complete, make_state

from agents import build
from econocontext.assembler import validate_protocol
from econocontext.config import Config
from econocontext.contracts import FeasibilityError, Limits, Mode, RunRequest, Task, Worker
from econocontext.runtime.manager import Manager
from econocontext.runtime.telemetry import build_profiles


@pytest.mark.parametrize("adapter", ["coding", "research"])
@pytest.mark.parametrize("method", ["react", "econocontext"])
async def test_verified_fixtures(manager, adapter, method):
    run = await complete(manager, adapter, method)
    assert run["status"] == "succeeded", run["reason"]
    assert run["verification"] == "verified"
    attempts = await manager.memory.records(run["id"], "attempt")
    assemblies = [e for e in await manager.memory.all_events(run["id"]) if e["kind"] == "assembly"]
    models = [a for a in attempts if a["kind"] == "model"]
    assert len(assemblies) == len(models)
    for attempt in models:
        request = manager.memory.artifacts.read_json(attempt["request"])
        reconstructed = manager.assembler.reconstruct(request["manifest"])
        assert reconstructed == request["body"]
        validate_protocol(reconstructed["messages"])
    if method == "econocontext":
        metrics = await manager.telemetry.metrics(run["id"])
        assert [x["mode"] for x in metrics["comparisons"]] == ["FRESH", "REUSE"]
        reuse_op = metrics["comparisons"][1]["operation_id"]
        owned = [a for a in attempts if a["operation_id"] == reuse_op]
        assert owned and all(a["phase"] == "integration" for a in owned)
        workers = await manager.memory.records(run["id"], "worker")
        assert len(workers) == 2
        for worker in workers:
            validate_protocol(await manager.memory.history(worker["id"]))
        profiles = await build_profiles(manager.memory, [run["id"]])
        assert profiles["profiles"] and all(p["synthetic"] for p in profiles["profiles"])


async def test_inline_continuation(manager):
    run = await complete(manager, limits=Limits(max_children=0))
    assert run["status"] == "succeeded", run
    workers = await manager.memory.records(run["id"], "worker")
    assert len(workers) == 1
    metrics = await manager.telemetry.metrics(run["id"])
    assert metrics["comparisons"][0]["mode"] == "CONTINUE"
    op_id = metrics["comparisons"][0]["operation_id"]
    assert not [
        a
        for a in await manager.memory.records(run["id"], "attempt")
        if a["operation_id"] == op_id and a["phase"] == "integration"
    ]


async def test_broader_profile_drives_actual_execution(manager):
    operation, state = await make_state(manager)
    manager.planner.cost.profiles = dict(
        revision="synthetic-injected",
        defaults={
            "BROADER": dict(calls=1, output=16, latency=0.01),
            "FOCUSED": dict(calls=40, output=9000, latency=1),
            "CONTINUE": dict(calls=40, output=9000, latency=1),
        },
        profiles=[],
    )
    plan = await manager.planner.plan(operation, state)
    assert plan.view == "BROADER"
    await manager.execute(plan, operation, state, "request")
    children = [
        w for w in await manager.memory.records(operation.run_id, "worker") if w["role"] == "child"
    ]
    assert len(children) == 1
    assert len(children[0]["bindings"]) == 3
    assert await manager.memory.records(operation.run_id, "result")


async def test_prior_worker_and_stale_rejection(manager):
    operation, state = await make_state(manager)
    child = Worker(
        run_id=operation.run_id,
        role="child",
        parent_id=state["worker"].id,
        scope=operation.scope,
        fingerprint=state["fingerprint"],
        bindings=dict(operation.bindings),
    )
    await manager.memory.save("worker", child, child.scope)
    # Make root context expensive; existing child stays short and cheaper than FRESH.
    await manager.memory.append(
        child, dict(role="user", content="Previous compatible assignment completed.")
    )
    manager.planner.cost.profiles = dict(
        revision="synthetic-prior",
        profiles=[],
        defaults={
            "CONTINUE": dict(calls=3, output=8),
            "FOCUSED": dict(calls=40),
            "BROADER": dict(calls=40),
        },
    )
    # Prior child has slightly longer context; root gets a large system-independent history.
    history = await manager.memory.history(state["worker"].id)
    await manager.memory.write("DELETE FROM messages WHERE worker_id=?", (state["worker"].id,))
    await manager.memory.append(
        state["worker"], dict(role="user", content="earlier context " * 1000)
    )
    for message in history:
        await manager.memory.append(state["worker"], message)
    plan = await manager.planner.plan(operation, state)
    assert plan.mode == Mode.CONTINUE and plan.worker_id == child.id
    await manager.execute(plan, operation, state, "request")
    results = await manager.memory.records(operation.run_id, "result")
    assert results[0]["worker_id"] == child.id
    state["adapter"].path("policy.txt").write_text("Changed fuel inputs")
    await state["adapter"].refresh()
    state["versions"] = await manager.memory.versions(operation.run_id)
    matches = await manager.memory.find_candidates(operation, state)
    candidates = manager.planner.generator.generate(operation, state, matches)
    assert all(c.mode != Mode.REUSE and c.worker_id != child.id for c in candidates)


async def test_final_assembly_reselects_before_call(manager):
    operation, state = await make_state(manager)
    # Metadata underestimation cannot result in submission of an overfull prompt.
    original = manager.assembler.assemble
    blocked = []

    async def reject_fresh(worker, plan, current):
        if plan and plan.mode == Mode.FRESH:
            blocked.append(plan.id)
            raise FeasibilityError("Injected final tokenizer overflow")
        return await original(worker, plan, current)

    manager.assembler.assemble = reject_fresh
    result = await manager.request_operation(
        state["worker"],
        state,
        dict(goal=operation.goal, scope=operation.scope, required=operation.required),
        "request",
    )
    assert blocked and result["inline"]
    assert not await manager.memory.records(operation.run_id, "attempt")
    events = await manager.memory.all_events(operation.run_id)
    assert any(
        e["kind"] == "planning" and e["trigger"] == "final-assembly-reselection" for e in events
    )


async def test_budget_and_no_feasible(manager):
    run = await complete(manager, limits=Limits(max_attempts=1))
    assert run["status"] == "budget-exceeded"
    run = await complete(manager, limits=Limits(latency=0.000001))
    assert run["status"] == "failed" and "No feasible" in run["reason"]
    run = await complete(manager, limits=Limits(max_cost=0))
    assert run["status"] == "budget-exceeded"


async def test_no_cross_run_reuse(manager):
    first = await complete(manager)
    second = await complete(manager)
    first_workers = {w["id"] for w in await manager.memory.records(first["id"], "worker")}
    second_workers = {w["id"] for w in await manager.memory.records(second["id"], "worker")}
    assert first_workers.isdisjoint(second_workers)
    selections = (await manager.telemetry.metrics(second["id"]))["comparisons"]
    assert selections[0]["mode"] == "FRESH"


async def test_parent_headroom_rejects_delegation(manager):
    operation, state = await make_state(manager)
    # Tight enough that no candidate survives: delegation loses parent headroom
    # and continuing does not fit either.
    state["limits"].context_tokens = 1500
    state["limits"].output_tokens = 1024
    with pytest.raises(FeasibilityError):
        await manager.planner.plan(operation, state)
    decisions = [
        e for e in await manager.memory.all_events(operation.run_id) if e["kind"] == "planning"
    ]
    assert any(
        "insufficient parent integration headroom" in c["rejections"]
        for c in decisions[0]["candidates"]
    )


async def test_harness_plans_without_a_delegation_request(tmp_path):
    """The planner must run on what the root is already doing.

    A live model is never offered request_operation, so if planning only
    happened on an explicit delegation request it would never happen at all.
    """
    import json

    from econocontext.contracts import ModelResponse

    class Direct:
        """Runs the visible tests, then finishes. Never asks to delegate."""

        def __init__(self):
            self.turn = 0

        async def complete(self, request, context):
            self.turn += 1
            name, args = ("test", {}) if self.turn == 1 else ("complete_task", {})
            if name == "complete_task":
                refs = context["evidence"][:1]
                args = dict(answer="Reported the failing checks.", evidence=refs)
            message = dict(
                role="assistant",
                content=None,
                tool_calls=[
                    dict(
                        id=f"c{self.turn}",
                        type="function",
                        function=dict(name=name, arguments=json.dumps(args)),
                    )
                ],
            )
            return ModelResponse(message=message, usage=None, synthetic=True)

    config = Config(data_dir=tmp_path, delegation_tool=False)
    manager = await Manager(config, Direct(), build).start()
    try:
        run = await manager.submit(
            RunRequest(task=Task(adapter="coding", fixture="ledger"), method="econocontext")
        )
        await manager.wait(run["id"])
        events = await manager.memory.all_events(run["id"])
        planning = [e for e in events if e["kind"] == "planning"]
        assert planning, "harness raised no operation from a large tool observation"
        assert any("tool-result" in e["trigger"] for e in planning)
        # The mechanism stays out of the model's head entirely: neither the
        # prompt nor the offered tool schemas mention it.
        from econocontext.assembler import Assembler

        assembly = next(e for e in events if e["kind"] == "assembly")
        sent = json.dumps(Assembler(manager.memory, config).reconstruct(assembly["manifest"]))
        assert "request_operation" not in sent
        assert "delegate" not in sent and "child" not in sent
    finally:
        await manager.close()


async def test_delegated_answer_is_smaller_than_the_literal_one(tmp_path):
    """The claim the whole mechanism rests on, asserted rather than argued.

    Delegation previously appended a finding *alongside* the full observation,
    so the root could only grow. A delegated answer must carry fewer tokens
    than the literal answer it replaces, while still answering the same call.
    """
    import json

    from econocontext.assembler import token_count
    from econocontext.contracts import ModelResponse
    from econocontext.planning.representation import message as tool_message

    class Reader:
        """Reads two large modules, then finishes. Never asks to delegate."""

        def __init__(self):
            self.turns = {}

        async def complete(self, request, context):
            worker = context["worker_id"]
            turn = self.turns.get(worker, 0)
            self.turns[worker] = turn + 1
            if context["active_operation"]:
                name, args = (
                    "complete_operation",
                    dict(
                        answer="The module defines bookkeeping helpers.",
                        evidence=context["evidence"][:1],
                    ),
                )
            elif turn == 0:
                name, args = "read", dict(path="ledger/parsing.py")
            elif turn == 1:
                name, args = "read", dict(path="ledger/validation.py")
            else:
                name, args = (
                    "complete_task",
                    dict(answer="Reviewed the modules.", evidence=context["evidence"][:1]),
                )
            return ModelResponse(
                message=dict(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        dict(
                            id=f"c{worker[:4]}{turn}",
                            type="function",
                            function=dict(name=name, arguments=json.dumps(args)),
                        )
                    ],
                ),
                usage=None,
                synthetic=True,
            )

    config = Config(data_dir=tmp_path, delegation_tool=False)
    manager = await Manager(config, Reader(), build).start()
    try:
        run = await manager.submit(
            RunRequest(
                task=Task(adapter="coding", fixture="ledger"),
                method="econocontext",
                limits=Limits(
                    context_tokens=8192,
                    plan_pressure=0.15,
                    observation_tokens=256,
                    max_attempts=20,
                ),
            )
        )
        final = await manager.wait(run["id"])
        events = await manager.memory.all_events(run["id"])
        swaps = [e for e in events if e["kind"] == "representation"]
        assert swaps, "no observation was ever delegated"
        for swap in swaps:
            assert swap["delivered_tokens"] < swap["inline_tokens"], swap

        # The call is still answered by its own result, not a different one.
        history = await manager.memory.history(final["root_worker"])
        delivered = [
            json.loads(m["content"])
            for m in history
            if m.get("role") == "tool" and "finding" in str(m.get("content"))
        ]
        assert delivered, "delegated payload never reached the root"
        for payload in delivered:
            assert payload["truncated"] is True
            assert payload["evidence"] and payload["original"]
            assert payload["finding"]
            # And it is genuinely smaller than the literal answer would have been.
            assert token_count(tool_message("x", payload), 1.2) < token_count(
                tool_message("x", dict(payload, text="y" * 6000)), 1.2
            )
    finally:
        await manager.close()
