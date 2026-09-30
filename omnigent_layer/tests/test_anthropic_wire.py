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
