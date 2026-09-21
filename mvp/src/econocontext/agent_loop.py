"""One loop for root/children and both methods; no independent execution policy."""

import asyncio
import json
import time

import httpx

from .contracts import FeasibilityError, canonical


def backoff(exc, attempt_index):
    """Seconds to wait before retrying; honours Retry-After when the provider sends one."""
    if isinstance(exc, httpx.HTTPStatusError):
        header = exc.response.headers.get("retry-after")
        if header:
            try:
                return min(float(header), 60.0)
            except ValueError:
                pass
    # Retrying a rate limit immediately just spends another attempt on the same refusal.
    return min(2.0**attempt_index, 30.0)


def reject(exc):
    """A malformed completion is recoverable: tell the worker how to retry."""
    if isinstance(exc, KeyError):
        return (
            f"Unknown evidence reference {exc}. Use the exact id strings returned in the "
            "evidence field of tool results, copied verbatim and unmodified."
        )
    return str(exc)


class AgentLoop:
    def __init__(self, manager):
        self.manager = manager

    async def run(self, worker, state, plan=None, operation=None):
        memory, manager = self.manager.memory, self.manager
        while True:
            await manager.check(state)
            await state["adapter"].refresh()
            if operation and not await memory.compatible(worker.run_id, operation.bindings):
                raise FeasibilityError(
                    "Active operation inputs changed; controlled stop before model request"
                )
            assembled = await manager.assembler.assemble(worker, plan, state)
            if worker.role == "root" and state["method"] == "econocontext":
                state["root_tokens"] = assembled.tokens
                limits = state["limits"]
                state["pressure"] = assembled.tokens >= limits.context_tokens * limits.plan_pressure
                if state["pressure"]:
                    # Flagged a turn before it bites, so the next derived
                    # observation can be moved out rather than carried.
                    adapter = state["adapter"]
                    narrowed = adapter.output_budget != limits.pressured_output_chars
                    adapter.output_budget = limits.pressured_output_chars
                    if narrowed:
                        await memory.event(
                            worker.run_id,
                            "context_pressure",
                            dict(
                                tokens=assembled.tokens,
                                budget=limits.context_tokens,
                                fill=round(assembled.tokens / limits.context_tokens, 3),
                                output_chars=limits.pressured_output_chars,
                            ),
                        )
            retry_of = None
            integration = (
                state.get("integration") if worker.role == "root" and operation is None else None
            )
            owner_op = operation.id if operation else (integration[0] if integration else None)
            owner_plan = plan.id if plan else (integration[1] if integration else None)
            phase = "integration" if integration else "execution"
            for retry in range(state["limits"].retries + 1):
                if retry:
                    assembled = await manager.assembler.assemble(worker, plan, state)
                await manager.check(state, model=True, input_tokens=assembled.tokens)
                attempt = await manager.telemetry.begin(
                    worker.run_id,
                    worker_id=worker.id,
                    kind="model",
                    name=manager.config.model,
                    request={"manifest": assembled.manifest, "body": assembled.request},
                    operation_id=owner_op,
                    plan_id=owner_plan,
                    phase=phase,
                    retry_of=retry_of,
                )
                start = time.monotonic()
                state["attempts"] += 1
                try:
                    evidence_map = await memory.bindings(worker.run_id)
                    refs = list(evidence_map.values())
                    response = await manager.backend.complete(
                        assembled.request,
                        dict(
                            worker_id=worker.id,
                            adapter=state["adapter"].name,
                            method=state["method"],
                            evidence=refs,
                            active_operation=operation is not None,
                            evidence_map=evidence_map,
                        ),
                    )
                    completed = await manager.telemetry.finish(
                        attempt,
                        duration=time.monotonic() - start,
                        status="succeeded",
                        response=response.message,
                        raw_usage=response.usage,
                    )
                    state["known_cost"] += completed["cost"] or 0
                    state["last_attempt"] = attempt["id"]
                    break
                except asyncio.CancelledError:
                    await manager.telemetry.finish(
                        attempt,
                        duration=time.monotonic() - start,
                        status="cancelled",
                        error="Remote outcome/usage may be unknown",
                    )
                    raise
                except Exception as exc:
                    await manager.telemetry.finish(
                        attempt,
                        duration=time.monotonic() - start,
                        status="failed",
                        error=type(exc).__name__,
                    )
                    retry_of = attempt["id"]
                    retryable = isinstance(exc, (TimeoutError, httpx.TransportError)) or (
                        isinstance(exc, httpx.HTTPStatusError)
                        and exc.response.status_code in (429, 500, 502, 503, 504)
                    )
                    if retry == state["limits"].retries or not retryable:
                        raise
                    await asyncio.sleep(backoff(exc, retry))
            await manager.check(state)
            if integration:
                state.pop("integration", None)
                await memory.event(
                    worker.run_id,
                    "integration_complete",
                    dict(operation_id=owner_op, plan_id=owner_plan),
                )
            await memory.append(worker, response.message)
            calls = response.message.get("tool_calls", [])
            if not calls:
                raise ValueError(
                    "Model must use structured completion; plain prose is not verified completion"
                )
            # A control action cannot share a response with other calls; acknowledge errors.
            control = {"request_operation", "complete_operation", "complete_task"}
            if len(calls) > 1 and any(c["function"]["name"] in control for c in calls):
                for call in calls:
                    await memory.append(
                        worker,
                        dict(
                            role="tool",
                            tool_call_id=call["id"],
                            content=canonical(
                                {"error": "Control action must be the sole tool call"}
                            ),
                        ),
                    )
                continue
            observed = []
            for call in calls:
                name = call["function"]["name"]
                observation, observed_refs, args = None, [], None
                try:
                    args = json.loads(call["function"]["arguments"])
                    if not isinstance(args, dict):
                        raise ValueError("Tool arguments must be an object")
                    allowed = {
                        t["function"]["name"]
                        for t in state["adapter"].tools(worker, state["method"])
                    }
                    if name not in allowed:
                        raise PermissionError("Tool is not allowed for this worker")
                except (ValueError, PermissionError) as exc:
                    observed.append(
                        dict(
                            call_id=call["id"],
                            name=name,
                            arguments=None,
                            output={"error": str(exc)},
                            observation=None,
                            refs=[],
                        )
                    )
                    continue
                if name == "request_operation":
                    if operation or state.get("active_operation"):
                        output = {"error": "Nested operation requests are not supported in V1"}
                    else:
                        result = await manager.request_operation(worker, state, args, call["id"])
                        if result.get("inline"):
                            operation, plan = result["operation"], result["plan"]
                        # Manager closes the pending call before inline or parent continuation.
                        continue
                elif name == "complete_operation":
                    if operation is None:
                        output = {"error": "No active operation"}
                    else:
                        try:
                            await manager.validate_result(args, operation, state)
                        except (ValueError, KeyError) as exc:
                            output = {"error": reject(exc)}
                            await memory.append(
                                worker,
                                dict(
                                    role="tool",
                                    tool_call_id=call["id"],
                                    content=canonical(output),
                                ),
                            )
                            continue
                        await memory.append(
                            worker,
                            dict(
                                role="tool",
                                tool_call_id=call["id"],
                                content=canonical({"accepted": True}),
                            ),
                        )
                        result = await manager.complete_operation(
                            worker, operation, plan, args, state
                        )
                        if worker.role == "child":
                            return result
                        operation, plan = None, None
                        state.pop("active_operation", None)
                        continue
                elif name == "complete_task":
                    if operation:
                        output = {"error": "Complete the active operation first"}
                    else:
                        try:
                            await manager.validate_result(args, None, state)
                        except (ValueError, KeyError) as exc:
                            output = {"error": reject(exc)}
                            await memory.append(
                                worker,
                                dict(
                                    role="tool",
                                    tool_call_id=call["id"],
                                    content=canonical(output),
                                ),
                            )
                            continue
                        await memory.append(
                            worker,
                            dict(
                                role="tool",
                                tool_call_id=call["id"],
                                content=canonical({"accepted": True}),
                            ),
                        )
                        return args
                else:
                    # `test` runs a subprocess through the same path as `command`,
                    # so it carries the same effect uncertainty.
                    if name in ("command", "test"):
                        state["reuse_uncertain"] = True
                    await manager.check(state)
                    attempt = await manager.telemetry.begin(
                        worker.run_id,
                        worker_id=worker.id,
                        kind="tool",
                        name=name,
                        request=args,
                        operation_id=operation.id if operation else None,
                        plan_id=plan.id if plan else None,
                    )
                    start = time.monotonic()
                    try:
                        output = await asyncio.wait_for(
                            state["adapter"].execute(name, args, worker),
                            state["limits"].tool_timeout,
                        )
                        await manager.telemetry.finish(
                            attempt,
                            duration=time.monotonic() - start,
                            status="succeeded",
                            response=output,
                        )
                    except asyncio.CancelledError:
                        await manager.telemetry.finish(
                            attempt,
                            duration=time.monotonic() - start,
                            status="cancelled",
                            error="Tool interrupted; effect may be uncertain",
                        )
                        raise
                    except Exception as exc:
                        output = {"error": type(exc).__name__ + ": " + str(exc)}
                        await manager.telemetry.finish(
                            attempt,
                            duration=time.monotonic() - start,
                            status="failed",
                            response=output,
                            error=type(exc).__name__,
                        )
                    observation = await memory.observe(
                        worker.run_id,
                        "tool:" + attempt["id"],
                        canonical(output),
                        attempt["id"],
                        "tool-output",
                    )
                    await state["adapter"].refresh()
                    observed_refs = list(output.get("evidence", []))
                    observed_refs.extend(
                        m["evidence"] for m in output.get("matches", []) if "evidence" in m
                    )
                    for ref in observed_refs:
                        evidence = await memory.get(ref, worker.run_id)
                        worker.bindings[evidence["source"]] = evidence["version"]
                    # The writer observes its own changes; other contexts remain stale.
                    if worker.role == "root":
                        versions = await memory.versions(worker.run_id)
                        worker.bindings = {
                            key: versions[key] for key in worker.bindings if key in versions
                        }
                observed.append(
                    dict(
                        call_id=call["id"],
                        name=name,
                        arguments=args,
                        output=output,
                        observation=observation,
                        refs=observed_refs,
                    )
                )

            # Executing a call and answering it are separate decisions. What answers
            # it is a representation, and choosing one needs every observation this
            # turn produced -- including the ones that will answer sibling calls,
            # since a preview assembled while any call is unanswered is not valid.
            payloads = (
                await manager.represent(worker, state, observed)
                if worker.role == "root" and operation is None and state["method"] == "econocontext"
                else [item["output"] for item in observed]
            )
            for item, payload in zip(observed, payloads):
                message = dict(
                    role="tool", tool_call_id=item["call_id"], content=canonical(payload)
                )
                if item["observation"] is not None:
                    message["_evidence"] = (
                        [item["observation"].id]
                        + item["refs"]
                        + list(payload.get("finding_evidence", []))
                    )
                await memory.append(worker, message)
