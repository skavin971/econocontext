from omnigent_layer import wire
from econocontext.types import SegmentKind

# Shapes observed from Vertex's OpenAI-compatible endpoint on 2026-09-28 (question 0b).
FIRST = {"prompt_tokens": 14236, "completion_tokens": 1, "total_tokens": 14268,
         "completion_tokens_details": {"reasoning_tokens": 31}}
SECOND = {"prompt_tokens": 14236, "completion_tokens": 1, "total_tokens": 14265,
          "prompt_tokens_details": {"cached_tokens": 10212},
          "completion_tokens_details": {"reasoning_tokens": 28}}


def test_cached_tokens_become_cache_read():
    u = wire.to_usage(SECOND)
    assert (u.uncached_input, u.cache_read) == (14236 - 10212, 10212)


def test_absent_cache_details_mean_not_reported_not_zero():
    u = wire.to_usage(FIRST)
    assert u.cache_read is None and u.uncached_input == 14236


def test_reasoning_outside_completion_is_added_to_output():
    assert wire.to_usage(FIRST).output == 1 + 31


def test_reasoning_inside_completion_is_not_counted_twice():
    openai_style = {"prompt_tokens": 10, "completion_tokens": 50, "total_tokens": 60,
                    "completion_tokens_details": {"reasoning_tokens": 40}}
    assert wire.to_usage(openai_style).output == 50


def test_segments_and_round_trip_keep_original_messages():
    body = {"tools": [{"type": "function", "function": {"name": "t"}}], "messages": [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "fix the bug"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
        {"role": "user", "content": "more"}]}
    segments, index = wire.to_segments("r", "r:root", body)
    assert [s.kind for s in segments] == [SegmentKind.TOOLS, SegmentKind.SYSTEM, SegmentKind.TASK,
                                          SegmentKind.TOOL_CALL, SegmentKind.TOOL_RESULT,
                                          SegmentKind.MESSAGE]
    messages = wire.from_segments(segments, body, index)
    assert all(a is b for a, b in zip(messages, body["messages"]))
