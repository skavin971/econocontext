"""The interface every policy shares (harness/interface.py): the same system prompt and tools for every
policy, no context-management text, one tool call per reply, the nudges, cut replies, and the legacy
interface of steps 9 and 10 for reproducing old runs."""

from baselines.base.policy import Base
from baselines.summary.policy import Summary
from econocontext.policy import Econo
from harness.interface import LEGACY, OURS, TOOLS
from tests.harness.helpers import reply, tool_messages


def test_every_policy_sends_the_identical_system_prompt_and_tools(make, agentdb):
    sent = []
    for policy, extra in ((Base(), {}), (Base(), {"budget": 32_000, "counter": lambda m, t: 1_000}),
                          (Summary(), {"budget": 32_000, "counter": lambda m, t: 1_000}),
                          (Econo(mode="prior", dsn=agentdb), {"run_id": "t1"})):
        agent, _, _ = make([reply(("submit", {}))], policy=policy, **extra)
        sent.append((agent.client.sent[0][0], agent.client.tools[0]))
    assert all(s == ({"role": "system", "content": OURS.system}, TOOLS) for s in sent)


def test_the_shared_interface_has_no_context_management_text():
    texts = [OURS.system, OURS.nudge, OURS.nudge_cut, OURS.reject_extra, OURS.cut_call]
    for word in ("context", "budget", "compact", "summar", "token"):
        assert not any(word in t.lower() for t in texts), word
    assert len(OURS.system.split()) < 60          # short


def test_only_the_first_tool_call_of_a_reply_runs(make):
    agent, env, _ = make([reply(("bash", {"command": "echo first"}), ("bash", {"command": "echo second"})),
                          reply(("submit", {}))])
    out = tool_messages(agent)
    assert out[0].startswith("first\n(exit code 0)") and out[1] == OURS.reject_extra
    assert not any("echo second" in c for c in env.commands)


def test_the_nudge_says_whether_the_reply_made_no_call_or_was_cut_off(make):
    agent, _, _ = make([reply(text="thinking..."), reply(text=None, finish="length", completion_tokens=2048),
                        reply(("submit", {}))])
    users = [m["content"] for m in agent.messages if m["role"] == "user"][1:]
    assert users == [OURS.nudge, OURS.nudge_cut]


def test_a_cut_reply_runs_no_tool_call(make):
    # vLLM returns a cut call with its cut parameter dropped (step 10b): it is not run.
    agent, env, _ = make([reply(("write_file", {"path": "a.txt"}), finish="tool_calls", completion_tokens=2048),
                          reply(("submit", {}))])
    assert tool_messages(agent) == [OURS.cut_call, "Submitted."]
    assert not any("base64" in c for c in env.commands)


def test_the_legacy_interface_runs_every_call_with_the_old_texts(make):
    agent, env, _ = make([reply(("bash", {"command": "echo first"}), ("bash", {"command": "echo second"})),
                          reply(text="thinking..."),
                          reply(("bash", {"command": "echo cut"}), completion_tokens=2048),
                          reply(("submit", {}))], interface="legacy")
    assert agent.client.sent[0][0]["content"] == LEGACY.system
    out = tool_messages(agent)
    assert out[0].startswith("first") and out[1].startswith("second") and out[2].startswith("cut")
    assert {"role": "user", "content": LEGACY.nudge} in agent.messages
