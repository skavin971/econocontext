"""Single-process lifecycle, pool and dispatch shared by API and CLI."""

import asyncio
import time

from . import representation
from .adapters.local import LocalAdapter
from .agent_loop import AgentLoop
from .assembler import Assembler, token_count
from .backends.openai import OpenAIBackend
from .backends.scripted import ScriptedBackend
from .config import Pricing
from .contracts import (
    FeasibilityError,
    Mode,
    Operation,
    Result,
    RunRequest,
    Status,
    StopRun,
    Worker,
    canonical,
    now,
)
from .memory import MemoryStore
from .planner import Planner
from .telemetry import Telemetry

# A tool request supplies the lookup keys for the operation it implies: its name,
# its arguments and the versions of what it touched. The goal interpolates only
# the scope and the immutable task goal -- never anything from the worker's
# context, because key() omits context and reuse would otherwise be unsound.
OBSERVATIONS = {
    "read": (
        "analysis",
        lambda a: a["path"],
        "Report what {scope} contains that bears on: {task}",
    ),
    "search": (
        "research",
        lambda a: "search:" + a.get("query", ""),
        "Report what a search for {scope} found that bears on: {task}",
    ),
    "test": (
        "diagnosis",
        lambda a: "tests",
        "Explain why the checks reported what they did, for: {task}",
    ),
    "command": (
        "diagnosis",
        lambda a: "command:" + " ".join(a.get("argv", []))[:80],
        "Explain what running {scope} reported, for: {task}",
    ),
}


class Manager:
    def __init__(self, config, backend=None):
        if config.backend == "scripted" and config.pricing is None:
            config = config.model_copy(
                update={
                    "pricing": Pricing(
                        revision="synthetic-v1",
                        input_per_million=1,
                        cached_per_million=0.25,
                        output_per_million=2,
                    )
                }
            )
        self.config = config
        self.memory = MemoryStore(config.data_dir)
        self.telemetry = Telemetry(self.memory, config)
        self.assembler = Assembler(self.memory, config)
        self.planner = Planner(self.memory, config, self.assembler)
        self.loop = AgentLoop(self)
        self.backend = backend or (
            ScriptedBackend() if config.backend == "scripted" else OpenAIBackend(config)
        )
        self.queue = asyncio.Queue()
        self.cancelled = set()
        self.active_id, self.active_task = None, None
        self.runner = None

    async def start(self):
        await self.memory.open()
        for row in await self.memory.query(
            "SELECT id,status FROM runs WHERE status IN ('running','queued')"
        ):
            if row["status"] == "running":
                await self.memory.update_run(
                    row["id"],
                    status="interrupted",
                    ended=now(),
                    reason="Process restarted; side effects were not replayed",
                )
            else:
                await self.queue.put(row["id"])
        self.runner = asyncio.create_task(self._runner())
        return self

    async def close(self):
        if self.runner:
            self.runner.cancel()
            await asyncio.gather(self.runner, return_exceptions=True)
        await self.memory.close()

    async def submit(self, request: RunRequest):
        if (
            self.config.backend == "openai"
            and request.limits.context_tokens > self.config.live_context_tokens
        ):
            raise ValueError("Requested context exceeds configured live model capacity")
        run, created = await self.memory.create_run(request, self.config.fingerprint())
        if created:
            await self.queue.put(run["id"])
        return run

    async def wait(self, run_id):
        while True:
            run = await self.memory.run(run_id)
            if run["status"] not in ("queued", "running"):
                return run
            await asyncio.sleep(0.01)

    async def cancel(self, run_id):
        run = await self.memory.run(run_id)
        if run["status"] not in ("queued", "running"):
            return dict(run_id=run_id, status=run["status"], cancellation="already-terminal")
        self.cancelled.add(run_id)
        if self.active_id == run_id and self.active_task:
            self.active_task.cancel()
        else:
            await self.memory.update_run(
                run_id, status="cancelled", ended=now(), reason="Cancelled while queued"
            )
        return dict(run_id=run_id, status=run["status"], cancellation="requested")

    async def _runner(self):
        while True:
            run_id = await self.queue.get()
            try:
                if (await self.memory.run(run_id))["status"] != "queued":
                    continue
                self.active_id = run_id
                self.active_task = asyncio.create_task(self._run(run_id))
                await asyncio.shield(self.active_task)
            except asyncio.CancelledError:
                if self.active_task:
                    self.active_task.cancel()
                    await asyncio.gather(self.active_task, return_exceptions=True)
                raise
            finally:
                self.active_id, self.active_task = None, None
                self.queue.task_done()

    async def _run(self, run_id):
        run = await self.memory.run(run_id)
        request = RunRequest(**run["request"])
        state = dict(
            run_id=run_id,
            limits=request.limits,
            method=request.method,
            fingerprint=self.config.fingerprint(),
            attempts=0,
            known_cost=0,
            start=time.monotonic(),
        )
        await self.memory.update_run(run_id, status="running", started=now())
        adapter = LocalAdapter(
            request.task,
            self.config.data_dir / "workspaces" / run_id,
            self.memory,
            run_id,
            request.limits.tool_timeout,
        )
        adapter.delegation_tool = bool(self.config.delegation_tool)
        state["adapter"] = adapter
        try:
            async with asyncio.timeout(request.limits.deadline):
                snapshot = await adapter.prepare()
                root = Worker(
                    run_id=run_id, scope=request.task.adapter, fingerprint=state["fingerprint"]
                )
                await self.memory.save("worker", root, root.scope)
                state["worker"] = root
                await self.memory.update_run(
                    run_id, root_worker=root.id, environment_snapshot=snapshot
                )
                await self.memory.append(root, dict(role="user", content=adapter.goal))
                result = await self.loop.run(root, state)
                check = await self.telemetry.begin(
                    run_id,
                    worker_id=root.id,
                    kind="tool",
                    name="fixture-verifier",
                    request=result,
                    phase="external_grader",
                )
                start = time.monotonic()
                verification = await adapter.verify(result)
                await self.telemetry.finish(
                    check,
                    duration=time.monotonic() - start,
                    status="succeeded",
                    response=verification,
                )
                artifact = await adapter.export()
                status = "succeeded" if verification["status"] != "failed" else "failed"
                await self.memory.update_run(
                    run_id,
                    status=status,
                    ended=now(),
                    outcome=result,
                    verification=verification["status"],
                    verification_details=verification,
                    final_artifact=artifact,
                )
        except asyncio.CancelledError:
            status = "cancelled" if run_id in self.cancelled else "interrupted"
            await self.memory.update_run(
                run_id,
                status=status,
                ended=now(),
                reason="Scheduling stopped; in-flight effects or usage may be uncertain",
            )
        except TimeoutError:
            await self.memory.update_run(
                run_id, status="budget-exceeded", ended=now(), reason="Run deadline exceeded"
            )
        except StopRun as exc:
            await self.memory.update_run(
                run_id, status=exc.status.value, ended=now(), reason=exc.reason
            )
        except Exception as exc:
            await self.memory.update_run(
                run_id, status="failed", ended=now(), reason=f"{type(exc).__name__}: {exc}"
            )
        finally:
            for record in await self.memory.records(run_id, "operation"):
                if record["status"] == "running":
                    record["status"] = (await self.memory.run(run_id))["status"]
                    await self.memory.save("operation", record)

    async def check(self, state, model=False, input_tokens=0):
        if state["run_id"] in self.cancelled:
            raise StopRun(Status.CANCELLED, "Cancellation requested")
        limits = state["limits"]
        if time.monotonic() - state["start"] >= limits.deadline:
            raise StopRun(Status.BUDGET, "Run deadline exceeded")
        if model and state["attempts"] >= limits.max_attempts:
            raise StopRun(Status.BUDGET, "Maximum model attempts reached")
        if limits.max_cost is not None:
            reserve = 0
            if model and self.config.pricing:
                reserve = (
                    input_tokens * self.config.pricing.input_per_million
                    + limits.output_tokens * self.config.pricing.output_per_million
                ) / 1e6
            if state["known_cost"] + reserve > limits.max_cost:
                raise StopRun(Status.BUDGET, "Insufficient spending headroom for next bounded call")

    async def validate_result(self, result, operation, state):
        if not isinstance(result.get("answer"), str) or not isinstance(
            result.get("evidence"), list
        ):
            raise ValueError("Completion requires answer string and evidence references")
        maximum = operation.result_tokens if operation else state["limits"].output_tokens
        if token_count(result, state["limits"].safety_margin) > maximum:
            raise FeasibilityError("Result exceeds bounded output contract")
        for ref in result["evidence"]:
            evidence = await self.memory.get(ref, state["run_id"])
            if "version" not in evidence:
                raise ValueError("Result reference is not evidence")

    def _planning_limits(self, state):
        remaining = state["limits"].model_copy()
        if remaining.max_cost is not None:
            remaining.max_cost = max(0, remaining.max_cost - state["known_cost"])
        remaining.latency = min(
            remaining.latency, max(0.001, remaining.deadline - (time.monotonic() - state["start"]))
        )
        return remaining

    def note_observation(self, state, item):
        """Is this observation worth an operation? Pure; returns lookup keys or None."""
        name, output = item["name"], item["output"]
        if name not in OBSERVATIONS or item["observation"] is None:
            return None  # apply_patch mutates the workspace; a mutation is not an operation
        size = token_count(output, state["limits"].safety_margin)
        if size < state["limits"].observation_tokens:
            return None
        return dict(
            name=name,
            arguments=item["arguments"],
            size=size,
            evidence=list(item["refs"]) or [item["observation"].id],
        )

    async def represent(self, worker, state, observed):
        """Choose what answers each tool call, between executing and answering.

        The default is the literal result. A selected plan may substitute a
        bounded view of that same result plus a finding -- never a different
        result, and never a larger one.
        """
        payloads = [item["output"] for item in observed]
        requests = [self.note_observation(state, item) for item in observed]
        eligible = [i for i, request in enumerate(requests) if request]
        if not eligible or state.get("plans", 0) >= state["limits"].max_plans:
            return payloads
        # One operation per turn: a batched turn must not spawn two children.
        index = max(eligible, key=lambda i: requests[i]["size"])
        item, request = observed[index], requests[index]
        triggers = {"tool-result"}
        if state.get("pressure"):
            triggers.add("context-pressure")
        if isinstance(item["output"], dict) and (
            item["output"].get("error") or item["output"].get("code")
        ):
            triggers.add("failure")
        operation = await self.derive_operation(worker, state, request, triggers)
        if operation is None:
            return payloads
        margin = state["limits"].safety_margin
        state["plans"] = state.get("plans", 0) + 1
        state["pressure_selected"] = "context-pressure" in triggers
        state.update(
            worker=worker,
            active_operation=operation.id,
            versions=await self.memory.versions(worker.run_id),
            planning_limits=self._planning_limits(state),
            observation=dict(
                call_id=item["call_id"],
                name=item["name"],
                output=item["output"],
                inline=representation.message(item["call_id"], item["output"]),
                # Siblings this turn will answer. A projection omitting them is
                # not protocol-valid, and every candidate would be rejected.
                tail=[
                    representation.message(other["call_id"], other["output"])
                    for other in observed[index + 1 :]
                ],
                chars=representation.excerpt_chars(
                    item["call_id"], item["name"], item["output"], operation, margin
                ),
            ),
        )
        await self.memory.save("operation", operation)
        try:
            result = await self._select_and_execute(
                operation, state, item["call_id"], ",".join(sorted(triggers))
            )
            payloads[index] = result.get("payload", item["output"])
        except (asyncio.CancelledError, StopRun):
            raise
        except Exception as exc:
            # A derived operation is an optimisation the worker never asked for,
            # so nothing it does may fail the run: a plan that will not fit, or a
            # child that answers badly, leaves the literal result answering the
            # call exactly as it would have without any of this. Record why,
            # because a silently abandoned optimisation looks identical to one
            # that was never attempted.
            state.pop("active_operation", None)
            operation.status = "failed"
            await self.memory.save("operation", operation)
            await self.memory.event(
                worker.run_id,
                "operation_abandoned",
                dict(
                    operation_id=operation.id,
                    scope=operation.scope,
                    reason=f"{type(exc).__name__}: {exc}",
                ),
            )
        finally:
            state.pop("observation", None)
            state.pop("pressure_selected", None)
        return payloads

    async def derive_operation(self, worker, state, request, triggers):
        if not request or "tool-result" not in triggers:
            return None
        kind, scope_of, template = OBSERVATIONS[request["name"]]
        scope = scope_of(request["arguments"])
        bindings = {}
        for ref in request["evidence"]:
            try:
                evidence = await self.memory.get(ref, worker.run_id)
            except KeyError:
                continue
            bindings[evidence["source"]] = evidence["version"]
        return Operation(
            run_id=worker.run_id,
            worker_id=worker.id,
            origin="observation",
            kind=kind,
            goal=template.format(scope=scope, task=state["adapter"].goal),
            scope=scope,
            arguments=request["arguments"],
            required=request["evidence"],
            bindings=bindings,
            # Sized against the observation it competes with, so a delegated
            # finding can never be larger than the bytes it saves the root.
            result_tokens=max(32, min(512, request["size"] // 2)),
            status="running",
        )

    async def request_operation(self, worker, state, args, call_id):
        await state["adapter"].refresh()
        versions = await self.memory.versions(worker.run_id)
        required = args.get("required", [])
        bindings = {}
        for ref in required:
            evidence = await self.memory.get(ref, worker.run_id)
            bindings[evidence["source"]] = evidence["version"]
        operation = Operation(
            run_id=worker.run_id,
            worker_id=worker.id,
            goal=args["goal"],
            scope=args["scope"],
            kind=args.get("kind", "analysis"),
            required=required,
            bindings=bindings,
            status="running",
        )
        state["planning_limits"] = self._planning_limits(state)
        state.update(versions=versions, worker=worker, active_operation=operation.id)
        await self.memory.save("operation", operation)
        return await self._select_and_execute(operation, state, call_id)

    async def _select_and_execute(self, operation, state, call_id, trigger="operation-boundary"):
        worker = state["worker"]
        rejected, exact = set(), {}
        for cycle in range(12):
            await self.check(state)
            state["exact_tokens"] = exact
            plan = await self.planner.plan(
                operation,
                state,
                rejected,
                trigger if cycle == 0 else "final-assembly-reselection",
            )
            try:
                result = await self.execute(plan, operation, state, call_id, preflight=True)
                if result.get("tokens") is not None and abs(
                    result["tokens"] - plan.estimate.tokens
                ) > max(64, plan.estimate.tokens * 0.1):
                    exact[plan.label()] = result["tokens"]
                    continue
            except FeasibilityError as exc:
                rejected.add(plan.label())
                await self.memory.event(
                    worker.run_id, "assembly_rejected", dict(plan_id=plan.id, reason=str(exc))
                )
                continue
            operation.selected_plan = plan.id
            await self.memory.save("operation", operation)
            await self.memory.event(
                worker.run_id, "selection", dict(plan=plan.model_dump(mode="json"))
            )
            return await self.execute(plan, operation, state, call_id)
        raise FeasibilityError("Assembly reselection limit reached")

    @staticmethod
    def delivery(call_id, payload):
        return representation.message(call_id, payload)

    def answer(self, state, operation, call_id, finding, mode):
        """The message that closes the pending call, and the payload inside it.

        A model-requested operation is told what it asked for. A harness-raised
        one is answered by a bounded view of its own tool result, and only if
        that view is genuinely smaller -- the guard is what makes the claim
        checkable rather than argued, and it fails to the literal result.
        """
        view = state.get("observation")
        if operation.origin == "request" or not view:
            payload = dict(operation_result=finding, mode=mode)
            return self.delivery(call_id, payload), payload
        payload = representation.represent(view["name"], view["output"], finding, view["chars"])
        candidate = self.delivery(call_id, payload)
        margin = state["limits"].safety_margin
        if token_count(candidate, margin) >= token_count(view["inline"], margin):
            return view["inline"], view["output"]
        return candidate, payload

    @staticmethod
    def tail(state):
        return list((state.get("observation") or {}).get("tail", []))

    async def execute(self, plan, operation, state, call_id, preflight=False):
        root, memory = state["worker"], self.memory
        await state["adapter"].refresh()
        if not await memory.compatible(operation.run_id, operation.bindings):
            raise FeasibilityError("Required source changed before execution")
        if plan.mode == Mode.REUSE:
            result = Result(**await memory.get(plan.result_id, operation.run_id))
            if (
                not result.reusable
                or result.operation_key != operation.key(state["fingerprint"])
                or not await memory.compatible(operation.run_id, result.requirements)
            ):
                raise FeasibilityError("Stored result no longer applicable")
            output = memory.artifacts.read_json(result.payload)
            await self.validate_result(output, operation, state)
            if preflight:
                # Parent-only validation, no dummy worker and no child prompt.
                parent, _ = self.answer(state, operation, call_id, output, "REUSE")
                await self.assembler.assemble(
                    root,
                    None,
                    dict(
                        state,
                        preview=True,
                        history=(await memory.history(root.id)) + [parent] + self.tail(state),
                    ),
                )
                return {}
            operation.status = "succeeded"
            operation.ended = now()
            await memory.save("operation", operation)
            await memory.event(
                operation.run_id,
                "operation_outcome",
                dict(operation_id=operation.id, mode="REUSE", result_id=result.id),
            )
            message, payload = self.answer(state, operation, call_id, output, "REUSE")
            # A derived operation's call is answered by agent_loop, which owns
            # `_evidence`; appending here too would double-answer the same id.
            if operation.origin == "request":
                await memory.append(root, message)
            state.pop("active_operation", None)
            state["integration"] = (operation.id, plan.id)
            return {"inline": False, "payload": payload}
        worker = (
            Worker(**await memory.get(plan.worker_id, operation.run_id))
            if plan.worker_id
            else Worker(
                run_id=operation.run_id,
                parent_id=root.id,
                role="child",
                scope=operation.scope,
                fingerprint=state["fingerprint"],
            )
        )
        if not await memory.compatible(operation.run_id, worker.bindings):
            raise FeasibilityError("Selected worker is stale")
        additions = await self.assembler.additions(plan, state)
        assignment = dict(
            role="user",
            content="Operation: " + operation.goal + "\nContract: " + operation.output_contract,
        )
        history = await memory.history(worker.id) if plan.worker_id else []
        inline = worker.id == root.id
        if inline and operation.origin != "request":
            # The root already holds this observation. "Keep reading it yourself"
            # is the null plan: it is priced like any other candidate, but
            # executing it means doing nothing the root can perceive.
            if preflight:
                # The null plan must be projected carrying the observation it
                # declines to move; otherwise the reselection loop overwrites
                # candidate_tokens and erases the very cost being compared.
                view = state.get("observation")
                current = await self.assembler.assemble(
                    root,
                    None,
                    dict(
                        state,
                        preview=True,
                        history=(await memory.history(root.id))
                        + ([view["inline"]] if view else [])
                        + self.tail(state),
                    ),
                )
                return {"tokens": current.tokens}
            operation.status, operation.ended = "succeeded", now()
            await memory.save("operation", operation)
            state.pop("active_operation", None)
            return {"inline": False}
        ack = dict(
            role="tool",
            tool_call_id=call_id,
            content=canonical(dict(inline=True, instruction=operation.goal)),
        )
        if inline:
            history += [ack]
        preview = dict(state, preview=True, history=history + additions + [assignment])
        assembled = await self.assembler.assemble(worker, plan, preview)
        if preflight:
            if not inline:
                # Projection >= delivery: it uses the contract's maximum finding,
                # and the real finding is validated against that same maximum.
                projected, _ = self.answer(
                    state,
                    operation,
                    call_id,
                    representation.finding_stub(operation, state["limits"].safety_margin),
                    plan.mode.value,
                )
                await self.assembler.assemble(
                    root,
                    None,
                    dict(
                        state,
                        preview=True,
                        history=(await memory.history(root.id)) + [projected] + self.tail(state),
                    ),
                )
            return {"tokens": assembled.tokens}
        if plan.mode == Mode.FRESH:
            children = [
                Worker(**w)
                for w in await memory.records(operation.run_id, "worker")
                if w["role"] == "child" and w["status"] != "retired"
            ]
            if len(children) >= state["limits"].max_children:
                idle = sorted([w for w in children if w.status == "idle"], key=lambda w: w.id)
                if not idle:
                    raise FeasibilityError("No idle child can be retired")
                idle[0].status = "retired"
                await memory.save("worker", idle[0], idle[0].scope)
            await memory.save("worker", worker, worker.scope)
        worker.status = "running"
        for ref in plan.evidence:
            evidence = await memory.get(ref, operation.run_id)
            if (await memory.versions(operation.run_id)).get(evidence["source"]) != evidence[
                "version"
            ]:
                raise FeasibilityError("Selected evidence changed before execution")
            worker.bindings[evidence["source"]] = evidence["version"]
        if inline:
            await memory.append(worker, ack)
        for message in additions + [assignment]:
            await memory.append(worker, message)
        if inline:
            # Keep the same worker object used by the outer root loop.
            root.bindings, root.revision = worker.bindings, worker.revision
            return dict(inline=True, operation=operation, plan=plan)
        result = await self.loop.run(worker, state, plan, operation)
        output = memory.artifacts.read_json(result.payload)
        message, payload = self.answer(state, operation, call_id, output, plan.mode.value)
        if operation.origin == "request":
            await memory.append(root, message)
        else:
            view = state["observation"]
            await memory.event(
                operation.run_id,
                "representation",
                dict(
                    operation_id=operation.id,
                    mode=plan.mode.value,
                    inline_tokens=token_count(view["inline"], state["limits"].safety_margin),
                    delivered_tokens=token_count(message, state["limits"].safety_margin),
                    excerpt_chars=view["chars"],
                ),
            )
        state.pop("active_operation", None)
        state["integration"] = (operation.id, plan.id)
        return {"inline": False, "payload": payload}

    async def complete_operation(self, worker, operation, plan, output, state):
        await state["adapter"].refresh()
        requirements = dict(operation.bindings)
        requirements.update(worker.bindings)
        if not await self.memory.compatible(operation.run_id, requirements):
            raise FeasibilityError("Inputs changed before result publication")
        result = Result(
            run_id=operation.run_id,
            operation_key=operation.key(state["fingerprint"]),
            worker_id=worker.id,
            attempt_id=state.get("last_attempt"),
            payload=self.memory.artifacts.json(output),
            evidence=output["evidence"],
            requirements=requirements,
            fingerprint=state["fingerprint"],
            reusable=operation.reusable and operation.kind in ("analysis", "diagnosis", "research"),
        )
        await self.memory.save("result", result, result.operation_key)
        operation.status = "succeeded"
        operation.ended = now()
        worker.status = "idle"
        await self.memory.save("operation", operation)
        await self.memory.save("worker", worker, worker.scope)
        await self.memory.event(
            operation.run_id,
            "operation_outcome",
            dict(operation_id=operation.id, result_id=result.id, mode=plan.mode.value),
        )
        return result
