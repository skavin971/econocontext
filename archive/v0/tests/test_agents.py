"""Agents: tools, workspace containment, and what a tool result carries.

Owned by the agent workstream -- src/agents/."""

import pytest
from conftest import complete, make_state
from fake_model import ScriptedBackend

from agents import build
from econocontext.config import Config
from econocontext.contracts import Limits
from econocontext.runtime.manager import Manager


async def test_tool_timeout_scope_and_original_output(manager):
    operation, state = await make_state(manager)
    adapter = state["adapter"]
    adapter.timeout = 0.03
    import sys

    with pytest.raises(TimeoutError):
        await adapter.command([sys.executable, "-c", "import time; time.sleep(10)"])
    assert not adapter.processes
    with pytest.raises(PermissionError):
        adapter.path("../escape")
    adapter.timeout = 5
    result = await adapter.command([sys.executable, "-c", "print('x'*20000)"])
    assert result["truncated"] and len(manager.memory.artifacts.get(result["original"])) > 20000


async def test_tool_timeout_is_recorded(tmp_path):
    from econocontext.contracts import ModelResponse

    class TimeoutToolBackend(ScriptedBackend):
        async def complete(self, request, context):
            if self.turns[context["worker_id"]] == 0:
                self.turns[context["worker_id"]] += 1
                return ModelResponse(
                    message={
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "slow",
                                "type": "function",
                                "function": {
                                    "name": "command",
                                    "arguments": '{"argv":["python","-c","import time; time.sleep(10)"]}',
                                },
                            }
                        ],
                    },
                    usage={
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "prompt_tokens_details": {"cached_tokens": 0},
                    },
                    synthetic=True,
                )
            return await super().complete(request, context)

    manager = await Manager(Config(data_dir=tmp_path), TimeoutToolBackend(), build).start()
    try:
        run = await complete(manager, method="react", limits=Limits(tool_timeout=0.05))
        attempts = await manager.memory.records(run["id"], "attempt")
        assert any(
            a["name"] == "command" and a["error"] == "TimeoutError" and a["status"] == "failed"
            for a in attempts
        )
        assert run["status"] == "succeeded", run
    finally:
        await manager.close()
