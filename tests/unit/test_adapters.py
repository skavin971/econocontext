"""The Deep Agents adapter on plain data. No LLM: a replay model returns recorded
message shapes, chosen by what it is asked (so thread order does not matter)."""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from adapters.deepagents import (DeepAgentsHost, install_decisions, install_measurement,
                                 root_agent_id)
from adapters.deepagents.translate import from_segments, to_segments
from adapters.providers.gemini_usage import to_provider_usage
from econocontext.types import SegmentKind

# Real usage dicts recorded from Gemini 3.6 Flash via langchain-google-genai 4.4.0 (2026-09-26).
RECORDED_WARM = {"input_tokens": 12671, "output_tokens": 21, "total_tokens": 12692,
                 "input_token_details": {"cache_read": 10213},
                 "output_token_details": {"reasoning": 20}}
RECORDED_COLD = {"input_tokens": 2, "output_tokens": 72, "total_tokens": 74,
                 "input_token_details": {"cache_read": 0}, "output_token_details": {"reasoning": 71}}


def test_gemini_usage_maps_to_disjoint_categories():
    u = to_provider_usage(RECORDED_WARM, latency_ms=900)
    assert (u.uncached_input, u.cache_read, u.cache_write, u.output, u.reasoning) == \
        (2458, 10213, None, 21, 20)
    assert u.prompt_tokens == 12671 and u.raw == RECORDED_WARM and u.latency_ms == 900
    cold = to_provider_usage(RECORDED_COLD)
    assert cold.uncached_input == 2 and cold.cache_read == 0 and cold.output == 72
    assert to_provider_usage(None).output is None  # not reported: never zero


def test_translation_keeps_kinds_pairs_and_original_objects():
    call = AIMessage(content="", id="a1", tool_calls=[{"id": "c1", "name": "read_file",
                                                       "args": {"file_path": "/x.py"}}])
    msgs = [HumanMessage(content="Fix it", id="h1"), call,
            ToolMessage(content="print(1)", tool_call_id="c1", name="read_file", id="t1")]
    segs, originals = to_segments("r", "r:root", SystemMessage(content="sys"), [], msgs)
    assert [s.kind for s in segs] == [SegmentKind.SYSTEM, SegmentKind.TASK, SegmentKind.TOOL_CALL,
                                      SegmentKind.TOOL_RESULT]
    assert segs[2].pair_id == "c1" and segs[3].pair_id == "c1"
    back = from_segments(segs, originals)
    assert all(a is b for a, b in zip(back, msgs))  # unchanged: the same objects are sent


class ReplayModel(BaseChatModel):
    """Returns recorded message shapes chosen by the request, not generated text."""

    @property
    def _llm_type(self):
        return "replay"

    def bind_tools(self, tools, **kw):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        first_human = next(m for m in messages if isinstance(m, HumanMessage)).text
        usage = {"input_tokens": 100, "output_tokens": 5, "total_tokens": 105,
                 "input_token_details": {"cache_read": 0}}
        if first_human.startswith("find "):   # a subagent: answer its own task
            msg = AIMessage(content=f"answer to {first_human}", usage_metadata=usage)
        elif any(isinstance(m, ToolMessage) for m in messages):  # the root, after its tools
            msg = AIMessage(content="done", usage_metadata=usage)
        else:                                  # the root's first turn: two parallel tasks
            msg = AIMessage(content="", usage_metadata=usage, tool_calls=[
                {"id": "c1", "name": "task", "args": {"description": "find A",
                                                      "subagent_type": "general-purpose"}},
                {"id": "c2", "name": "task", "args": {"description": "find B",
                                                      "subagent_type": "general-purpose"}}])
        return ChatResult(generations=[ChatGeneration(message=msg)])


def test_two_parallel_task_calls_map_to_the_right_subagents(engine, monkeypatch):
    from deepagents import create_deep_agent
    from deepagents.backends import StateBackend
    from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT

    eco = engine(mode="observe")
    host = DeepAgentsHost(StateBackend())
    make = install_decisions(eco, host)
    seen = []  # (agent id, the first human message it planned)
    original = eco.plan_prompt

    def spy(agent_id, request):
        seen.append((agent_id, next(s.text for s in request.segments if s.role == "user")))
        return original(agent_id, request)

    monkeypatch.setattr(eco, "plan_prompt", spy)
    model = ReplayModel()
    gp = {**GENERAL_PURPOSE_SUBAGENT, "model": model, "tools": [], "middleware": [make()]}
    agent = create_deep_agent(model=model, backend=StateBackend(), subagents=[gp],
                              middleware=[make()])
    agent.invoke({"messages": [("user", "Fix the bug")]},
                 {"callbacks": install_measurement(eco)})
    by_task = {text: agent_id for agent_id, text in seen}
    assert by_task["find A"] == f"{eco.run_id}:task:c1"
    assert by_task["find B"] == f"{eco.run_id}:task:c2"
    assert by_task["Fix the bug"] == root_agent_id(eco)
    # Measurement attributes every call to the same agents, independently of the middleware.
    rows = eco.db.rows("SELECT agent_id, COUNT(*) AS n FROM outcomes GROUP BY agent_id")
    assert {r["agent_id"]: r["n"] for r in rows} == {
        root_agent_id(eco): 2, f"{eco.run_id}:task:c1": 1, f"{eco.run_id}:task:c2": 1}
    tree = {r["agent_id"]: r["parent_id"] for r in eco.db.rows("SELECT agent_id, parent_id FROM agents")}
    assert tree[f"{eco.run_id}:task:c1"] == root_agent_id(eco)
