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


async def test_messages_backend_contract_without_network(tmp_path):
    """Claude's Messages API behind the same run, priced at Vertex Sonnet 5 rates.

    The response shape (thinking block with signature, three-way input usage,
    thinking inside output_tokens) mirrors the Messages API.
    """
    import copy
    import json

    from econocontext.runtime.backend import MessagesBackend

    sent = []

    class Messages:
        async def create(self, **kwargs):
            sent.append(copy.deepcopy(kwargs))
            n = len(sent)
            if n == 1:
                name, args = "read", {"path": "policy.txt"}
            else:
                result = kwargs["messages"][-1]["content"][0]
                assert result["type"] == "tool_result"
                name, args = (
                    "complete_task",
                    {
                        "answer": "Fuel expenditure fell from 100 to 60 units.",
                        "evidence": json.loads(result["content"])["evidence"],
                    },
                )

            class Response:
                def model_dump(self, mode=None):
                    return {
                        "content": [
                            {"type": "thinking", "thinking": "", "signature": f"sig-{n}"},
                            {"type": "tool_use", "id": f"toolu_{n}", "name": name, "input": args},
                        ],
                        "usage": {
                            "input_tokens": 10,
                            "cache_read_input_tokens": 1000 if n > 1 else 0,
                            "cache_creation_input_tokens": 200 if n > 1 else 1000,
                            "output_tokens": 50,
                            "output_tokens_details": {"thinking_tokens": 30},
                        },
                    }

            return Response()

    class Client:
        messages = Messages()

    config = Config(
        data_dir=tmp_path,
        backend="live",
        api="messages",
        model="claude-sonnet-5",
        live_context_tokens=16384,
        # Vertex, global endpoint, 2026-09-25: input 2.00, 5m write 2.50, hit 0.20, output 10.00
        pricing=Pricing(
            input_per_million=2.00,
            cached_per_million=0.20,
            cache_write_per_million=2.50,
            output_per_million=10.00,
        ),
    )
    manager = await Manager(config, MessagesBackend(config, client=Client()), build).start()
    try:
        run = await complete(manager, "research", "react")
        assert run["verification"] == "verified", run
        first, second = sent
        # Static prefix closed by one explicit breakpoint; the tail cached automatically.
        assert first["system"][-1]["cache_control"] == {"type": "ephemeral"}
        assert first["cache_control"] == {"type": "ephemeral"}
        assert all("input_schema" in tool for tool in first["tools"])
        # The assistant turn goes back byte-identical, thinking signature included.
        assistant = second["messages"][1]
        assert assistant["role"] == "assistant"
        assert assistant["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig-1"}
        metrics = await manager.telemetry.metrics(run["id"])
        tokens = metrics["tokens"]
        assert tokens["cached"] == 1000 and tokens["cache_write"] == 1200
        assert tokens["uncached"] == 20 and tokens["output"] == 100
        # 20*2.00 + 1000*0.20 + 1200*2.50 + 100*10.00, per million
        assert metrics["known_cost"] == pytest.approx(0.00424)
    finally:
        await manager.close()


def test_implicit_cache_write_bills_at_input_rate():
    from econocontext.runtime.telemetry import charge

    usage = normalize(
        dict(
            prompt_tokens=100,
            completion_tokens=10,
            prompt_tokens_details={"cached_tokens": 40, "cache_write_tokens": 30},
        )
    )
    assert (usage["uncached"], usage["cached"], usage["cache_write"]) == (30, 40, 30)
    pricing = Pricing(input_per_million=1, cached_per_million=0.1, output_per_million=2)
    assert charge(usage, pricing) == pytest.approx((60 * 1 + 40 * 0.1 + 10 * 2) / 1e6)


def test_unparsed_harmony_tool_call_is_recovered_and_marked():
    import json

    from econocontext.runtime.backend import recover_tool_call, wire

    leaked = {
        "role": "assistant",
        "content": "<|start|>assistant<|channel|>analysis to=functions.read code<|message|>"
        '{"path":"pyflakes/checker.py"}<|call|>',
    }
    message = recover_tool_call(leaked)
    call = message["tool_calls"][0]["function"]
    assert call["name"] == "read" and json.loads(call["arguments"]) == {
        "path": "pyflakes/checker.py"
    }
    assert message["_recovered"] == "harmony-text" and message["content"] is None
    # Prose stays prose, and the harness's own keys never reach the provider.
    prose = {"role": "assistant", "content": "I think the bug is in checker.py"}
    assert recover_tool_call(prose) is prose
    sent = wire({"model": "m", "messages": [message]})
    assert "_recovered" not in sent["messages"][0]
    # Reasoning is kept on the record but never sent back; empty call lists are dropped.
    thought = {"role": "assistant", "content": "x", "reasoning_content": "r", "tool_calls": []}
    assert wire({"model": "m", "messages": [thought]})["messages"][0] == {
        "role": "assistant",
        "content": "x",
    }


async def test_root_turn_cap_counts_only_the_root(tmp_path):
    import time

    from econocontext.contracts import StopRun

    manager = Manager(Config(data_dir=tmp_path))
    state = dict(
        run_id="r",
        limits=Limits(max_attempts=100, max_root_turns=3),
        start=time.monotonic(),
        attempts=50,  # children have made many calls
        root_calls=2,
        known_cost=0,
    )
    await manager.check(state, model=True, root=True)  # the root still has a turn
    await manager.check(state, model=True, root=False)  # children are not held to it
    state["root_calls"] = 3
    with pytest.raises(StopRun):
        await manager.check(state, model=True, root=True)
    await manager.check(state, model=True, root=False)
