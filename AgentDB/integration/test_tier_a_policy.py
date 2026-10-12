"""MemoryPolicy inside EconoContext's Tier A harness (branch tier-a-purdue), end to end with a
scripted model and a local shell: the second session of a task is served the first session's
read without running the tool, and its trajectory records where the answer came from.

Run from a checkout of that branch, in its own environment, with this package installed:
    PYTHONPATH=. venv/bin/python -m pytest <agent_db>/integration -p no:cacheprovider
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

pgserver = pytest.importorskip("pgserver")
harness = pytest.importorskip("harness.agent")
helpers = pytest.importorskip("tests.harness.helpers")
from baselines.base.policy import Base  # noqa: E402

from agent_db import PostgresAgentDB  # noqa: E402
from agent_db.memory import MemoryPolicy  # noqa: E402


@pytest.fixture(scope="module")
def dsn(tmp_path_factory):
    server = pgserver.get_server(tmp_path_factory.mktemp("pg"))
    yield server.get_uri()
    server.cleanup()


def session(tmp_path, monkeypatch, dsn, run_id):
    """One run of the harness on a two-step script: read a file, then submit."""
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda seconds: real_sleep(0))
    policy = MemoryPolicy(db=PostgresAgentDB(dsn), labels=["tm"], inner=Base())
    script = [helpers.reply(("read_file", {"path": "a.txt"})), helpers.reply(("submit", {}))]
    agent = harness.Agent(logs_dir=tmp_path / "logs" / run_id, model_name="openai/qwen3.8:27b",
                          client=helpers.FakeModel(script), state_dir=str(tmp_path / "state"),
                          policy=policy, run_id=run_id)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    (work / "a.txt").write_text("hello\nworld\n")
    env = helpers.LocalEnv(work)
    context = SimpleNamespace(n_input_tokens=0, n_cache_tokens=0, n_output_tokens=0)

    async def go():
        await agent.setup(env)
        await agent.run("Read a.txt and submit.", env, context)
    asyncio.run(go())
    trajectory = json.loads((tmp_path / "logs" / run_id / "trajectory.json").read_text())
    return agent, env, policy, trajectory


def test_the_second_session_is_served_the_first_sessions_read(tmp_path, monkeypatch, dsn):
    first, env1, _, traj1 = session(tmp_path, monkeypatch, dsn, "tm.memory.task-a.r1")
    assert any("a.txt" in c for c in env1.commands)          # the first session ran the tool
    assert traj1["report"]["served_calls"] == 0
    shown1 = [m["content"] for m in first.messages if m["role"] == "tool"]

    second, env2, policy, traj2 = session(tmp_path, monkeypatch, dsn, "tm.memory.task-a.r2")
    assert not any("a.txt" in c for c in env2.commands)      # served: the tool never ran
    shown2 = [m["content"] for m in second.messages if m["role"] == "tool"]
    assert shown2[0] == shown1[0]                              # byte-identical to the first session
    assert traj2["report"]["served_calls"] == 1
    assert traj2["report"]["served"][0]["source_run"] == "tm.memory.task-a.r1"
    assert traj2["report"]["pool"] == {"labels": ["tm"], "same_task": True, "only_passed": False}
    assert traj2["end"] == "submitted" and traj2["policy"] == "memory"


def test_a_different_task_is_never_served(tmp_path, monkeypatch, dsn):
    session(tmp_path, monkeypatch, dsn, "tm.memory.task-b.r1")
    _, env, _, traj = session(tmp_path, monkeypatch, dsn, "tm.memory.task-c.r1")
    assert any("a.txt" in c for c in env.commands) and traj["report"]["served_calls"] == 0
