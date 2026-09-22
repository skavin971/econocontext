"""Shared fixtures. The stand-in model lives in fake_model.py, not the library."""

import time

import pytest
from fake_model import ScriptedBackend

from agents import build
from econocontext.config import Config
from econocontext.contracts import Limits, Operation, RunRequest, Task, Worker
from econocontext.runtime.manager import Manager


@pytest.fixture
async def manager(tmp_path):
    instance = await Manager(Config(data_dir=tmp_path), ScriptedBackend(), build).start()
    yield instance
    await instance.close()


async def complete(manager, adapter="coding", method="econocontext", limits=None):
    run = await manager.submit(
        RunRequest(task=Task(adapter=adapter), method=method, limits=limits or Limits())
    )
    return await manager.wait(run["id"])


async def make_state(manager):
    # Construct a real local run/worker without enqueuing execution.
    request = RunRequest(task=Task(adapter="research"))
    run, _ = await manager.memory.create_run(request, manager.config.fingerprint())
    adapter = build(
        request.task, manager.config.data_dir / "planning", manager.memory, run["id"], 30
    )
    await adapter.prepare()
    worker = Worker(run_id=run["id"], scope="policy.txt", fingerprint=manager.config.fingerprint())
    await manager.memory.save("worker", worker, worker.scope)
    evidence = (await manager.memory.bindings(run["id"]))["policy.txt"]
    versions = await manager.memory.versions(run["id"])
    operation = Operation(
        run_id=run["id"],
        worker_id=worker.id,
        goal="fuel expenditure",
        scope="policy.txt",
        required=[evidence],
        bindings={"policy.txt": versions["policy.txt"]},
        status="running",
    )
    await manager.memory.save("operation", operation)
    await manager.memory.append(
        worker,
        dict(
            role="assistant",
            content=None,
            tool_calls=[
                dict(
                    id="request",
                    type="function",
                    function=dict(name="request_operation", arguments="{}"),
                )
            ],
        ),
    )
    state = dict(
        run_id=run["id"],
        worker=worker,
        adapter=adapter,
        limits=Limits(),
        method="econocontext",
        versions=versions,
        fingerprint=manager.config.fingerprint(),
        attempts=0,
        known_cost=0,
        start=time.monotonic(),
    )
    return operation, state
