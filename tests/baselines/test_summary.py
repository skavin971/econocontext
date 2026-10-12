"""The Summary baseline (baselines/summary/policy.py) in our harness, with a scripted model and sizes:
the trigger, the compaction call, the rebuilt conversation, a capped summary, and the Base rule after it."""

import pytest

from baselines.summary.policy import CODEX_COMPACT, CODEX_SUMMARY_PREFIX, Summary
from harness.agent import Agent
from harness.interface import OURS, TOOLS
from tests.harness.helpers import BySize, FakeModel, reply

SUMMARY_TEXT = "SUMMARY: listed the files; next, write the report."


def test_the_summary_triggers_at_75_percent_of_the_budget_and_not_before(make):
    # Budget 10,000: compaction at 7,500 (75% of the budget, not of the 7,952 limit). 7,499 sends as it is.
    sizes = {(2, True): 7_000, (4, True): 7_499, (6, True): 7_500, (7, False): 7_620, (3, True): 1_200, (5, True): 1_900}
    agent, _, _ = make([reply(("bash", {"command": "echo 1"})), reply(("bash", {"command": "echo 2"})),
                        reply(text=SUMMARY_TEXT, finish="stop"), reply(("bash", {"command": "echo 3"})),
                        reply(("submit", {}))],
                       policy=Summary(), budget=10_000, counter=BySize(sizes))
    model = agent.client
    assert [h["X-Econo-Call-Kind"] for h in model.headers] == ["agent", "agent", "summary", "agent", "agent"]
    # The compaction call: the whole conversation plus Codex's prompt as a user message, and no tools.
    assert model.sent[2][:6] == model.sent[1] + model.sent[2][4:6] and len(model.sent[2]) == 7
    assert model.sent[2][-1] == {"role": "user", "content": CODEX_COMPACT}
    assert model.tools[2] is None and model.tools[1] == TOOLS == model.tools[3]
    # The rebuilt conversation: the system prompt, the task, then Codex's prefix and the summary.
    assert model.sent[3] == [{"role": "system", "content": OURS.system}, {"role": "user", "content": "Make a file."},
                             {"role": "user", "content": CODEX_SUMMARY_PREFIX + "\n" + SUMMARY_TEXT}]
    assert agent.policy.compactions == [{"summary_call": 3, "P_before": 7_500, "P_call": 7_620, "G": 50, "capped": False,
                                         "finish_reason": "stop", "summary_chars": len(SUMMARY_TEXT), "P_after": 1_200,
                                         "P_after_share_of_budget": 0.12}]
    assert agent.end == "submitted"


def test_a_summary_that_hits_the_output_cap_is_flagged(make):
    # max_tokens 50 makes every scripted reply a cut one; the legacy interface still runs the agent's calls.
    sizes = {(2, True): 8_000, (3, False): 8_100, (3, True): 900, (5, True): 1_000}
    agent, _, _ = make([reply(text="SUMMARY: cut", finish="length"), reply(("bash", {"command": "echo 1"})),
                        reply(("submit", {}))],
                       policy=Summary(), budget=10_000, counter=BySize(sizes), max_tokens=50, interface="legacy")
    assert [c["capped"] for c in agent.policy.compactions] == [True]
    assert agent.policy.compactions[0]["finish_reason"] == "length"


def test_still_over_the_limit_after_compacting_the_base_rule_applies(make):
    # [system, task] 8,000 -> compaction (call 8,100) -> rebuilt 9,000, over -> final turn (notice, bash) -> 6
    # messages, 9,100 -> compaction again (call 9,200) -> rebuilt 9,000, still over after the final turn -> stop.
    sizes = {(2, True): 8_000, (3, False): 8_100, (3, True): 9_000, (6, True): 9_100, (7, False): 9_200}
    agent, _, _ = make([reply(text=SUMMARY_TEXT, finish="stop"), reply(("bash", {"command": "echo 1"})),
                        reply(text=SUMMARY_TEXT, finish="stop")],
                       policy=Summary(), budget=10_000, counter=BySize(sizes))
    assert len(agent.gate.final_turns) == 1 and agent.end == "budget_exceeded" and len(agent.policy.compactions) == 2


def test_the_summary_baseline_needs_a_budget(tmp_path):
    with pytest.raises(ValueError, match="the summary policy needs a budget"):
        Agent(logs_dir=tmp_path / "logs", model_name="openai/qwen3.8:27b", client=FakeModel([]), policy=Summary())
