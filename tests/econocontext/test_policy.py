"""EconoContext as a policy of our harness (econocontext/policy.py): it refuses a budget, Directed refuses to
run, and a prior-mode run goes through the Owner (its agent DB, decisions.jsonl and report)."""

import pytest
from agent_db import PostgresAgentDB

from econocontext import policy as policy_module
from econocontext.policy import Directed, Econo
from harness.agent import Agent
from tests.harness.helpers import FakeModel, reply


def test_a_budget_with_econocontext_is_refused(tmp_path):
    with pytest.raises(ValueError, match="EconoContext has no budget mechanism yet"):
        Agent(logs_dir=tmp_path / "logs", model_name="openai/qwen3.8:27b", client=FakeModel([]),
              policy=Econo(mode="prior"), budget=32_000)


def test_the_directed_policy_refuses_to_run():
    with pytest.raises(NotImplementedError, match="not built yet"):
        Directed()


def test_a_prior_mode_run_goes_through_the_owner(make, tmp_path, agentdb):
    agent, _, _ = make([reply(("bash", {"command": "echo hello"})), reply(("submit", {}))],
                       policy=Econo(mode="prior", dsn=agentdb), run_id="t1")
    assert agent.end == "submitted" and agent.policy.owner is not None
    logs = tmp_path / "logs"
    assert (logs / "decisions.jsonl").exists() and (logs / "trajectory.json").exists()
    assert not (logs / "session.sqlite3").exists() and isinstance(agent.policy.report(), dict)
    run = agent.policy.owner.run
    assert run.startswith("t1:") and PostgresAgentDB(agentdb).get_run(run)["task"] == "Make a file."
    window = PostgresAgentDB(agentdb).window(run)        # every message is a segment, in its place
    assert [(s.role, s.text) for s in window if not s.part] == [(m["role"], m.get("content") or "")
                                                                for m in agent.messages]


def test_each_trial_gets_its_own_run_in_the_agent_db(make, agentdb):
    runs = []
    for _ in range(2):
        agent, _, _ = make([reply(("submit", {}))], policy=Econo(mode="prior", dsn=agentdb), run_id="same")
        runs.append(agent.policy.owner.run)
    assert runs[0] != runs[1]


def test_without_a_dsn_it_refuses_to_start(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENTDB_DSN", raising=False)
    monkeypatch.setattr(policy_module, "ENV", tmp_path / "missing.env")
    with pytest.raises(RuntimeError, match="AGENTDB_DSN"):
        policy_module.agentdb_dsn()
    (tmp_path / "missing.env").write_text("OTHER=1\nAGENTDB_DSN=postgresql://u@h/db\n")
    assert policy_module.agentdb_dsn() == "postgresql://u@h/db"
