"""EconoContext as a policy of our harness (econocontext/policy.py): it refuses a budget, Directed refuses to
run, and a prior-mode run goes through the Owner (its session database and report)."""

import pytest

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


def test_a_prior_mode_run_goes_through_the_owner(make, tmp_path):
    agent, _, _ = make([reply(("bash", {"command": "echo hello"})), reply(("submit", {}))],
                       policy=Econo(mode="prior"), run_id="t1")
    assert agent.end == "submitted" and agent.policy.owner is not None
    assert (tmp_path / "logs" / "session.sqlite3").exists() and isinstance(agent.policy.report(), dict)
