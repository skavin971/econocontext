"""A compaction through the whole stack, as a run would do it. Our agent (agents/react/agent.py) owns
its conversation, with the fixed-guess predictor and compaction forced. It talks to the gateway
with the real openai client, and the gateway talks to a fake provider. The gateway's log must show:
- the prompts before the compaction;
- the summary call (kind summary);
- the prompts after it;
- no earlier reasoning in any request, although the agent keeps it in its own list.

No network beyond localhost.
"""

import asyncio
import json
from types import SimpleNamespace

from agents.react.agent import EconoAgent
from econocontext.owner import SUMMARIZE
from tests.agents.test_react_agent import LocalEnv
from tests.gateway.fakes import PINS, completion, log_rows, start


def provider():
    """Bash calls with reasoning until the 10th agent call, which submits; a summary request gets text."""
    count = [0]

    def reply(body):
        messages = body["messages"]
        tokens = len(json.dumps(messages)) // 4
        if messages[-1]["role"] == "user" and messages[-1]["content"] == SUMMARIZE:
            return 200, completion({"role": "assistant", "content": "SUMMARY: ran echo eight times; all fine."},
                                   prompt_tokens=tokens)
        count[0] += 1
        name, args = ("submit", {}) if count[0] >= 10 else ("bash", {"command": f"echo step {count[0]}"})
        return 200, completion({"role": "assistant", "content": None, "reasoning_content": f"thinking {count[0]}",
                                "tool_calls": [{"id": f"c{count[0]}", "type": "function",
                                                "function": {"name": name, "arguments": json.dumps(args)}}]},
                               prompt_tokens=tokens)
    return reply


def test_the_log_shows_the_prompts_before_and_after_a_compaction_and_the_summary_call(tmp_path):
    s = start(provider(), tmp_path / "gateway")
    try:
        agent = EconoAgent(logs_dir=tmp_path / "logs", model_name="openai/qwen3.8:27b",
                           api_base=f"{s.url}/run/t1/v1", econo="prior", econo_run="t1", force=["7 compact"],
                           state_dir=str(tmp_path / "state"))
        env, context = LocalEnv(tmp_path / "work"), SimpleNamespace(n_input_tokens=0, n_cache_tokens=0,
                                                                     n_output_tokens=0)
        (tmp_path / "work").mkdir()

        async def go():
            await agent.setup(env)
            await agent.run("Echo some steps, then submit.", env, context)
        asyncio.run(go())
    finally:
        s.stop()

    rows = log_rows(s.log_dir, "t1")
    kinds = [r["kind"] for r in rows]
    assert kinds == ["agent"] * 8 + ["summary"] + ["agent"] * 2 and [r["call_no"] for r in rows] == list(range(1, 12))
    before, summary, after = [r["request"]["messages"] for r in rows[:8]], rows[8]["request"]["messages"], \
        [r["request"]["messages"] for r in rows[9:]]
    assert [len(m) for m in before] == sorted({len(m) for m in before})            # the prompt grows, call by call
    assert summary[-1] == {"role": "user", "content": SUMMARIZE} and summary[:len(before[-1])] == before[-1]
    assert "SUMMARY: ran echo eight times" in after[0][1]["content"] and len(after[0]) < len(summary)
    assert after[0][0] == before[-1][0]                                             # the system prompt is untouched
    # Earlier reasoning: kept in the agent's own list, never sent upstream.
    assert any("reasoning_content" in m for m in agent.messages if m["role"] == "assistant")
    for r in rows:
        assert all("reasoning_content" not in m and "reasoning" not in m
                   for m in r["request"]["messages"] if m["role"] == "assistant")
        assert {k: r["request"][k] for k in PINS} == PINS
    assert agent.owner.s.get("compactions") == 1
