"""The `make` fixture: one run of our harness (harness/agent.py) with a scripted model and a local shell.
Base is the default policy and ours the default interface; a test passes others."""

import asyncio
from types import SimpleNamespace

import pytest

from baselines.base.policy import Base
from harness.agent import Agent
from tests.harness.helpers import FakeModel, LocalEnv


@pytest.fixture
def make(tmp_path, monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda seconds: real_sleep(0))

    def build(script, policy=None, **kwargs):
        agent = Agent(logs_dir=tmp_path / "logs", model_name="openai/qwen3.8:27b", client=FakeModel(script),
                      state_dir=str(tmp_path / "state"), policy=policy or Base(), **kwargs)
        env = LocalEnv(tmp_path / "work")
        (tmp_path / "work").mkdir(exist_ok=True)
        context = SimpleNamespace(n_input_tokens=0, n_cache_tokens=0, n_output_tokens=0)

        async def go():
            await agent.setup(env)
            await agent.run("Make a file.", env, context)
        asyncio.run(go())
        return agent, env, context
    return build
