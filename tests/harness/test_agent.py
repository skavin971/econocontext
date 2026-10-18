"""Our harness (harness/agent.py) with a scripted model and a local shell, under the Base policy.

The fake environment runs the harness's real bash commands in a temporary directory (it only drops
the `timeout` prefix, which macOS lacks), so tools, cwd persistence and the output cap are real.
The fake model returns real openai response objects, scripted per test. No network.
"""

import json
from types import SimpleNamespace

import pytest

from baselines.base.policy import Base
from harness.agent import Agent
from harness.budget import FINAL_TURN
from harness.interface import OURS
from harness.tools import cap
from tests.harness.helpers import Counts, FakeModel, StatusError, notices, reply, tool_messages


def test_tools_run_in_order_and_submit_ends_the_run(make, tmp_path):
    agent, env, context = make([
        reply(("bash", {"command": "mkdir sub && echo hi > sub/f.txt && cat sub/f.txt"})),
        reply(("write_file", {"path": "sub/g.txt", "content": "a\nb\nc\n"})),
        reply(("read_file", {"path": "sub/g.txt", "start_line": 2, "end_line": 3})),
        reply(("bash", {"command": "cd sub"})),
        reply(("bash", {"command": "pwd && ls"})),
        reply(("submit", {})),
    ])
    out = tool_messages(agent)
    assert out[0].startswith("hi\n(exit code 0)")
    assert "wrote 6 bytes to sub/g.txt" in out[1]
    assert out[2].startswith("2\tb\n3\tc\n[lines 2-3 of 3]")
    assert out[4].split("\n")[0].endswith("/work/sub") and "g.txt" in out[4]     # cd persisted
    assert out[5] == "Submitted." and agent.messages[-1]["role"] == "tool"
    assert (tmp_path / "logs" / "trajectory.json").exists()
    assert context.n_input_tokens == 6000 and context.n_cache_tokens == 3600 and len(agent.calls) == 6


def test_the_assistant_message_is_echoed_whole_and_the_list_never_ends_on_it(make):
    signature = {"google": {"thought_signature": "sig-123"}}
    agent, _, _ = make([reply(("bash", {"command": "echo x"}), extra=signature), reply(("submit", {}))])
    second_request = agent.client.sent[1]
    assistant = [m for m in second_request if m["role"] == "assistant"][0]
    assert assistant["extra_content"] == signature
    assert all(request[-1]["role"] != "assistant" for request in agent.client.sent)


def test_a_turn_without_a_tool_call_gets_a_nudge(make):
    agent, _, _ = make([reply(text="thinking..."), reply(("submit", {}))])
    assert agent.messages[3] == {"role": "user", "content": OURS.nudge}


def test_a_malformed_tool_call_is_resent_and_counted(make):
    agent, _, _ = make([reply(finish="malformed_function_call"), reply(("submit", {}))])
    assert len(agent.client.sent) == 2 and agent.client.sent[0] == agent.client.sent[1]
    assert [c["finish_reason"] for c in agent.calls] == ["malformed_function_call", "tool_calls"]


def test_transient_errors_are_retried_but_gateway_refusals_stop_the_run(make):
    agent, _, _ = make([StatusError(503), reply(("submit", {}))])
    assert tool_messages(agent) == ["Submitted."]
    with pytest.raises(StatusError):
        make([StatusError(429, "run r reached 60 model calls")])


def test_long_outputs_are_capped_head_and_tail():
    text = "a" * 50 + "b" * 50
    assert cap(text, 100) == text
    capped = cap(text, 20)
    assert capped.startswith("a" * 10) and capped.endswith("b" * 10) and "80 characters omitted" in capped


def test_a_reply_without_a_message_is_treated_as_a_turn_without_a_tool_call(make):
    empty = reply(("submit", {}))
    empty.choices[0].message = None          # what Gemini sent in gx1 raw malicious-package-forensics
    agent, _, _ = make([empty, reply(("submit", {}))])
    assert agent.messages[2] == {"role": "user", "content": OURS.nudge} and agent.messages[-1]["content"] == "Submitted."


def test_every_call_tells_the_gateway_its_kind(make):
    agent, _, _ = make([reply(("submit", {}))])
    assert agent.client.headers == [{"X-Econo-Call-Kind": "agent"}]


def test_without_an_api_base_the_agent_refuses_to_start(tmp_path):
    # Agents reach models only through the gateway: there is no default upstream to fall back to.
    with pytest.raises(ValueError, match="api_base is required"):
        Agent(logs_dir=tmp_path / "logs", model_name="openai/qwen3.8:27b", policy=Base())


def test_the_defaults_match_every_other_arm(tmp_path):
    # 64 steps (CLM's default); CLM's TBLite sampling and 2,048 output tokens for every arm (step 10b).
    agent = Agent(logs_dir=tmp_path / "logs", model_name="openai/qwen3.8:27b", client=FakeModel([]), policy=Base())
    assert (agent.max_steps, agent.max_tokens, agent.temperature, agent.top_p) == (64, 2048, 0.7, 0.95)


def test_harbor_output_count_is_completion_tokens_only(tmp_path):
    # vLLM's completion_tokens already includes the thinking; reasoning_tokens is a detail of it.
    agent = Agent(logs_dir=tmp_path / "logs", model_name="openai/qwen3.8:27b", client=FakeModel([]), policy=Base())
    agent.calls = [{"usage": {"prompt_tokens": 562, "completion_tokens": 44,
                              "completion_tokens_details": {"reasoning_tokens": 14}}}]
    context = SimpleNamespace(n_input_tokens=0, n_cache_tokens=0, n_output_tokens=0)
    agent._account(context)
    assert (context.n_input_tokens, context.n_output_tokens) == (562, 44)


def test_the_budget_rule_under_at_and_over_the_limit(make, tmp_path):
    # Budget 10,000: the limit is 10,000 - 2,048 = 7,952. Under and at it, calls go as they are. Over it,
    # the model gets ONE final turn with CLM's notice; still over at the next check, the run ends.
    agent, env, context = make([reply(("bash", {"command": "echo 1"})), reply(("bash", {"command": "echo 2"})),
                                reply(("bash", {"command": "echo 3"}))],
                               budget=10_000, counter=Counts([7_951, 7_952, 7_953, 7_990]))
    sent = agent.client.sent
    assert len(agent.calls) == 3 and not notices(sent[0]) and not notices(sent[1])
    assert notices(sent[2]) == [FINAL_TURN.format(tokens=7_953, budget=10_000)] == notices(agent.messages)
    assert sent[2][-1]["content"].startswith("[SYSTEM NOTICE] You have reached your context budget (7953/10000 tokens).")
    assert (agent.end, agent.end_P, agent.gate.final_turns) == ("budget_exceeded", 7_990, [{"before_call": 3, "P": 7_953}])
    saved = json.loads((tmp_path / "logs" / "trajectory.json").read_text())
    assert (saved["budget"], saved["end"], saved["end_P"]) == (10_000, "budget_exceeded", 7_990)


def test_back_under_the_limit_re_arms_the_final_turn(make):
    agent, _, _ = make([reply(("bash", {"command": "echo 1"})), reply(("bash", {"command": "echo 2"})),
                        reply(("bash", {"command": "echo 3"}))],
                       budget=10_000, counter=Counts([8_000, 7_000, 8_100, 8_200]))
    assert len(agent.calls) == 3 and len(notices(agent.messages)) == 2
    assert [f["P"] for f in agent.gate.final_turns] == [8_000, 8_100] and agent.end == "budget_exceeded"


def test_a_submit_in_the_final_turn_ends_the_run_normally(make):
    agent, _, _ = make([reply(("submit", {}))], budget=10_000, counter=Counts([8_000]))
    assert agent.end == "submitted" and len(agent.gate.final_turns) == 1 and len(agent.calls) == 1


def test_an_overflow_at_the_window_ends_the_run_without_an_exception(make):
    # The server refused the request for exceeding the model's window: the run ends, to be graded as is.
    refused = StatusError(400, "litellm.ContextWindowExceededError: maximum context length is 65536 tokens")
    agent, _, _ = make([reply(("bash", {"command": "echo 1"})), refused])
    assert agent.end == "overflow" and len(agent.calls) == 1


def test_running_out_of_steps_is_recorded(make):
    agent, _, _ = make([reply(("bash", {"command": "echo 1"})), reply(("bash", {"command": "echo 2"}))], max_steps=2)
    assert agent.end == "max_steps"


def test_the_budget_margin_keeps_a_safety_band_below_the_limit():
    # Step 13 (BrowseComp-Plus): count + max(0.5%, 64) must fit; without the margin (TBLite) the limit is exact.
    from harness.budget import BudgetGate
    plain, safe = BudgetGate(23_560), BudgetGate(23_560, margin=True)       # limit 21,512
    assert plain.allows([], 21_512, 1) and safe.allows([], 21_512 - 108, 1)  # 0.5% of 21,404 is 108 > 64
    notice = []
    assert safe.allows(notice, 21_512 - 100, 2) and notice and safe.final_turns[-1]["P"] == 21_412
    assert BudgetGate(5_000, margin=True).allows([], 2_888, 1) and not BudgetGate(5_000, margin=True).finalized
