"""Anthropic's Messages format: model calls, usage, tool results and replies."""

from omnigent_layer import anthropic_wire as aw


def test_only_messages_is_a_model_call():
    assert aw.model_request("/v1/messages", {"model": "claude-sonnet-5", "stream": True}) == \
        ("claude-sonnet-5", True)
    assert aw.model_request("/v1/messages/count_tokens", {"model": "m"}) is None
    assert aw.model_request("/v1/messages", "junk") is None


def test_usage_without_a_ttl_breakdown_is_all_5_minute_writes():
    u = aw.to_usage({"input_tokens": 5, "cache_creation_input_tokens": 300, "output_tokens": 9})
    assert (u.uncached_input, u.cache_read, u.cache_write, u.cache_write_1h, u.output) == (5, 0, 300, 0, 9)
    assert u.cache_write_applicable and u.reasoning is None
    assert aw.to_usage(None).uncached_input is None


def test_stream_usage_takes_the_latest_values():
    events = [{"type": "message_start", "message": {"usage": {"input_tokens": 3, "output_tokens": 1}}},
              {"type": "message_delta", "usage": {"output_tokens": 40}}]
    assert aw.extract_usage(events) == {"input_tokens": 3, "output_tokens": 40}


def test_tool_results_pair_by_id_and_carry_errors():
    body = {"messages": [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a", "name": "Read", "input": {"file_path": "x.py"}},
            {"type": "tool_use", "id": "b", "name": "Grep", "input": {"pattern": "y"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "b", "content": "x.py:1", "is_error": False},
            {"type": "tool_result", "tool_use_id": "a", "content": [{"type": "text", "text": "gone"}],
             "is_error": True}]}]}
    assert [(r["name"], r["args"], r["text"], r["error"]) for r in aw.tool_results(body)] == [
        ("Grep", {"pattern": "y"}, "x.py:1", False), ("Read", {"file_path": "x.py"}, "gone", True)]
    assert aw.tool_results({"messages": [{"role": "user", "content": "task"}]}) == []


def test_a_non_streamed_reply_and_a_sub_agent_context():
    calls, text = aw.reply([{"type": "message", "content": [
        {"type": "thinking", "thinking": "..."},
        {"type": "tool_use", "id": "t", "name": "Task", "input": {"prompt": "look"}}]}])
    assert calls == [{"name": "Task", "args": {"prompt": "look"}}] and text is False
    root = {"system": "main", "messages": [{"role": "user", "content": "task"}]}
    child = {"system": "sub-agent", "messages": [{"role": "user", "content": "look"}]}
    assert aw.context_key(root) != aw.context_key(child)


def test_a_real_claude_code_request():
    # The 6th request of the Claude Code smoke task (trimmed): Bash, Read, Edit, each followed
    # by a mid-conversation system message; the last assistant turn asked for Edit.
    import json
    from pathlib import Path
    body = json.loads((Path(__file__).parent / "fixtures" / "anthropic" /
                       "smoke_last_request.json").read_text())
    assert aw.model_request("/v1/messages", body) == ("claude-sonnet-5", True)
    [latest] = aw.tool_results(body)
    assert latest["name"] == "Edit" and not latest["error"]
    seen = aw.observe_request(body, json.dumps(body).encode())
    assert seen["contents"] == 11 and [f["name"] for f in seen["function_responses"]] == ["Edit"]
    assert "Agent" in {t["name"] for t in body["tools"]}  # Claude Code's sub-agent tool


def test_segments_of_a_real_request_pair_every_tool_call_with_its_result():
    import json
    from pathlib import Path
    from econocontext.types import SegmentKind as K
    body = json.loads((Path(__file__).parent / "fixtures" / "anthropic" /
                       "smoke_last_request.json").read_text())
    segs = aw.to_segments("r", "r:root", body)
    assert [s.kind for s in segs[:3]] == [K.TOOLS, K.SYSTEM, K.TASK]
    calls = {i for s in segs if s.kind == K.TOOL_CALL for i in s.pair_id.split(",")}
    results = {s.pair_id for s in segs if s.kind == K.TOOL_RESULT}
    assert calls == results and len(results) == 3
    assert {s.source for s in segs if s.kind == K.TOOL_RESULT} == {"tool:Bash", "tool:Read", "tool:Edit"}
