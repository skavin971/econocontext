"""Single-process lifecycle, pool and dispatch shared by API and CLI.

Running one bounded operation is the other half, in execution.py."""

import asyncio
import time

from ..assembler import Assembler
from ..config import Pricing
from ..context import build_policy
from ..contracts import (
    RunRequest,
    Status,
    StopRun,
    Worker,
    now,
)
from ..planning.planner import Planner
from ..store.memory import MemoryStore
from .agent_loop import AgentLoop
from .backend import build_backend
from .execution import OperationExecution
from .telemetry import Telemetry

CLEANUP_SECONDS = 60


class Manager(OperationExecution):
    def __init__(self, config, backend=None, adapter=None):
        if config.backend == "fake" and config.pricing is None:
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
        # One backend. A stand-in for tests is injected, never built here.
        self.backend = backend or build_backend(config)
        # The harness carries no domain knowledge: what tools exist, what
        # verification means and where data comes from are the agent's business.
        self.adapter_factory = adapter
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
        # A run's final status is written before its agent releases what it holds
        # (a task container). If that run has finished and is only cleaning up,
        # let the cleanup complete rather than cancel it half-way.
        task, run_id = self.active_task, self.active_id
        if task and not task.done() and run_id:
            if (await self.memory.run(run_id))["status"] not in ("queued", "running"):
                await asyncio.wait({task}, timeout=CLEANUP_SECONDS)
        if self.runner:
            self.runner.cancel()
            await asyncio.gather(self.runner, return_exceptions=True)
        await self.memory.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def submit(self, request: RunRequest):
        if (
            self.config.backend == "live"
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
            context_policy=build_policy(self.config),
        )
        await self.memory.update_run(run_id, status="running", started=now())
        if self.adapter_factory is None:
            raise ValueError("Manager needs an adapter factory; see econocontext.Harness")
        adapter = self.adapter_factory(
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
            # Agents holding outside resources (a task container) release them here.
            close = getattr(adapter, "close", None)
            if close:
                await close()
            for record in await self.memory.records(run_id, "operation"):
                if record["status"] == "running":
                    record["status"] = (await self.memory.run(run_id))["status"]
                    await self.memory.save("operation", record)

    async def check(self, state, model=False, input_tokens=0, root=False):
        if state["run_id"] in self.cancelled:
            raise StopRun(Status.CANCELLED, "Cancellation requested")
        limits = state["limits"]
        if time.monotonic() - state["start"] >= limits.deadline:
            raise StopRun(Status.BUDGET, "Run deadline exceeded")
        if model and state["attempts"] >= limits.max_attempts:
            raise StopRun(Status.BUDGET, "Maximum model attempts reached")
        if (
            model
            and root
            and limits.max_root_turns is not None
            and state.get("root_calls", 0) >= limits.max_root_turns
        ):
            raise StopRun(Status.BUDGET, "Maximum root turns reached")
        if limits.max_cost is not None:
            reserve = 0
            if model and self.config.pricing:
                pricing = self.config.pricing
                # Worst case: the whole prompt written to cache, where that costs more.
                input_rate = max(pricing.input_per_million, pricing.cache_write_per_million or 0)
                reserve = (
                    input_tokens * input_rate + limits.output_tokens * pricing.output_per_million
                ) / 1e6
            if state["known_cost"] + reserve > limits.max_cost:
                raise StopRun(Status.BUDGET, "Insufficient spending headroom for next bounded call")
