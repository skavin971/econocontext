from datetime import date

from econocontext import config
from econocontext.pricing.ledger import cost
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


def test_absent_cache_details_mean_nothing_cached():
    u = wire.to_usage(FIRST)
    assert u.cache_read == 0 and u.uncached_input == 14236
    assert "prompt_tokens_details" not in u.raw  # the absence stays visible for audit


def test_gemini_has_no_separate_cache_write_so_cost_is_complete():
    assert wire.to_usage(SECOND).cache_write_applicable is False


def test_ledger_prices_the_swebench_run_from_gateway_counts():
    # pytest-dev__pytest-5809 on Omnigent (2026-09-28): the gateway's totals over 28 calls.
    usage = wire.to_usage({"prompt_tokens": 90039 + 248415, "completion_tokens": 1261,
                           "total_tokens": 90039 + 248415 + 3082,
                           "prompt_tokens_details": {"cached_tokens": 248415},
                           "completion_tokens_details": {"reasoning_tokens": 1821}})
    _, usd, complete, _ = cost(usage, config.load("config").card, date(2026, 9, 28))
    assert complete and abs(usd - 0.097717875) < 1e-12


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
