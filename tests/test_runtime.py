"""Runtime: lifecycle, dispatch, accounting, the API, the store.

Shared. Check with the planning owner before changing behaviour here."""

import asyncio

import httpx
import pytest
from conftest import complete
from fake_model import ScriptedBackend

from agents import build
from econocontext.config import Config, Pricing
from econocontext.contracts import Limits, RunRequest
from econocontext.interfaces.api import create_app
from econocontext.runtime.manager import Manager
from econocontext.runtime.telemetry import normalize
from econocontext.store.memory import MemoryStore


async def test_api_lifecycle_idempotency(tmp_path):
    app = create_app(Config(data_dir=tmp_path), ScriptedBackend())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/health")).status_code == 200
            body = {"idempotency_key": "test-1", "task": {"adapter": "coding"}}
            first = await client.post("/runs", json=body)
            assert first.status_code == 202
            run_id = first.json()["run_id"]
            assert (await client.post("/runs", json=body)).json()["run_id"] == run_id
            assert (await client.post("/runs", json={**body, "method": "react"})).status_code == 409
            run = await app.state.manager.wait(run_id)
            assert run["verification"] == "verified", run
            assert (await client.get(f"/runs/{run_id}")).json()["status"] == "succeeded"
            page = (await client.get(f"/runs/{run_id}/trace?limit=2")).json()
            page2 = (
                await client.get(f"/runs/{run_id}/trace?after={page['next_cursor']}&limit=2")
            ).json()
            assert page2["events"][0]["seq"] > page["events"][-1]["seq"]
            assert (await client.get(f"/runs/{run_id}/metrics")).json()["cost_complete"]
            assert (await client.post(f"/runs/{run_id}/cancel")).json()[
                "cancellation"
            ] == "already-terminal"
            assert (await client.get("/runs/missing")).status_code == 404
            assert (await client.post("/runs", json={"method": "unknown"})).status_code == 422


async def test_attempt_accounting_retry_unknown_and_replay(tmp_path):
    manager = await Manager(Config(data_dir=tmp_path), ScriptedBackend(failures=1), build).start()
    try:
        run = await complete(manager)
        assert run["status"] == "succeeded"
        attempts = await manager.memory.records(run["id"], "attempt")
        failed = [a for a in attempts if a["status"] == "failed"]
        assert len(failed) == 1 and failed[0]["cost"] is None
        assert any(a["retry_of"] == failed[0]["id"] for a in attempts)
        before = await manager.telemetry.metrics(run["id"])
        assert not before["cost_complete"] and not before["usage_complete"]
        assert before["known_cost"] == sum(
            a["cost"] or 0 for a in attempts if a["phase"] != "external_grader"
        )
        for attempt in attempts:
            await manager.telemetry.finish(
                attempt,
                duration=999,
                status="succeeded",
                raw_usage={"prompt_tokens": 999, "completion_tokens": 999},
            )
        after = await manager.telemetry.metrics(run["id"])
        assert before == after
    finally:
        await manager.close()


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {"prompt_tokens": -1, "completion_tokens": 2},
        {"prompt_tokens": 3, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 4}},
    ],
)
def test_unknown_usage(raw):
    normalized = normalize(raw)
    assert not normalized["complete"] and normalized["uncached"] is None


def test_cache_disjoint():
    usage = normalize(
        dict(
            prompt_tokens=100,
            completion_tokens=20,
            prompt_tokens_details={"cached_tokens": 40},
            completion_tokens_details={"reasoning_tokens": 10},
        )
    )
    assert usage["uncached"] == 60 and usage["cached"] == 40 and usage["output"] == 20


async def test_cancel_and_deadline(tmp_path):
    manager = await Manager(Config(data_dir=tmp_path), ScriptedBackend(delay=0.5), build).start()
    try:
        run = await manager.submit(RunRequest())
        while not await manager.memory.records(run["id"], "attempt"):
            await asyncio.sleep(0.01)
        await manager.cancel(run["id"])
        final = await manager.wait(run["id"])
        assert final["status"] == "cancelled"
        attempts = await manager.memory.records(run["id"], "attempt")
        assert attempts[0]["status"] == "cancelled" and attempts[0]["cost"] is None
        run = await complete(manager, limits=Limits(deadline=0.05))
        assert run["status"] == "budget-exceeded"
    finally:
        await manager.close()


async def test_interrupted_startup(tmp_path):
    memory = await MemoryStore(tmp_path).open()
    run, _ = await memory.create_run(RunRequest(), "fixture")
    await memory.update_run(run["id"], status="running")
    await memory.close()
    manager = await Manager(Config(data_dir=tmp_path), ScriptedBackend(), build).start()
    try:
        assert (await manager.memory.run(run["id"]))["status"] == "interrupted"
        assert not await manager.memory.records(run["id"], "attempt")
    finally:
        await manager.close()


async def test_external_measurement_fixture(manager):
    run, _ = await manager.memory.create_run(
        RunRequest(method="react"), manager.config.fingerprint()
    )
    attempt = await manager.telemetry.begin(
        run["id"],
        worker_id="external-session",
        kind="model",
        name="external-fixture",
        request={"messages": []},
        attempt_id="external-stable-1",
    )
    await manager.telemetry.finish(
        attempt,
        duration=0.01,
        status="succeeded",
        raw_usage={"prompt_tokens": 10, "completion_tokens": 2},
    )
    assert attempt["operation_id"] is None and attempt["plan_id"] is None
    assert (await manager.telemetry.metrics(run["id"]))["model_attempts"] == 1


async def test_shutdown_active_run_is_interrupted(tmp_path):
    manager = await Manager(Config(data_dir=tmp_path), ScriptedBackend(delay=5), build).start()
    run = await manager.submit(RunRequest())
    while not await manager.memory.records(run["id"], "attempt"):
        await asyncio.sleep(0.01)
    await asyncio.wait_for(manager.close(), 2)
    memory = await MemoryStore(tmp_path).open()
    try:
        assert (await memory.run(run["id"]))["status"] == "interrupted"
        assert (await memory.records(run["id"], "attempt"))[0]["status"] == "cancelled"
    finally:
        await memory.close()


async def test_live_backend_contract_without_network(tmp_path, monkeypatch):
    import json

    from econocontext.runtime.backend import CompatibleBackend

    monkeypatch.setenv("TEST_MODEL_SECRET", "test-secret-never-record")
    requests = []

    async def endpoint(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.headers["authorization"] == "Bearer test-secret-never-record"
        assert body["max_completion_tokens"] == 1024
        if len(requests) == 1:
            name, args = "read", {"path": "policy.txt"}
        else:
            tool_output = json.loads(body["messages"][-1]["content"])
            name, args = (
                "complete_task",
                {
                    "answer": "Fuel expenditure fell from 100 to 60 units.",
                    "evidence": tool_output["evidence"],
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": f"live-{len(requests)}",
                                    "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(args)},
                                }
                            ],
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 40},
                },
            },
        )

    config = Config(
        data_dir=tmp_path,
        backend="live",
        model="mock-contract-model",
        credential_env="TEST_MODEL_SECRET",
        live_context_tokens=16384,
        pricing=Pricing(input_per_million=1, cached_per_million=0.25, output_per_million=2),
    )
    backend = CompatibleBackend(config, transport=httpx.MockTransport(endpoint))
    manager = await Manager(config, backend, build).start()
    try:
        run = await complete(manager, "research", "react")
        assert run["verification"] == "verified", run
        assert len(requests) == 2
        metrics = await manager.telemetry.metrics(run["id"])
        assert not metrics["synthetic"]
        assert metrics["known_cost"] == pytest.approx(0.00022)
        for artifact in manager.memory.artifacts.root.iterdir():
            assert b"test-secret-never-record" not in artifact.read_bytes()
    finally:
        await manager.close()
