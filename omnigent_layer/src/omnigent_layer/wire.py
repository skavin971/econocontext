"""OpenAI Chat Completions format <-> EconoContext types. Translation only.

Why it exists: every harness Omnigent runs on our Gemini key calls the gateway in this
one format (Vertex's OpenAI-compatible endpoint). This is the single place that turns
a request into segments, a rendered plan back into messages, and usage into
ProviderUsage.
What it must never do: decide anything, or change a message the plan did not change.

Usage mapping (checked against Vertex on 2026-09-28, question 0b):
  usage.prompt_tokens                          -> whole prompt, cached included
  usage.prompt_tokens_details.cached_tokens    -> cache_read. The details are ABSENT when
                                                  nothing was cached: recorded as "not
                                                  reported" (None), whole prompt uncached
  usage.completion_tokens                      -> visible output
  usage.completion_tokens_details.reasoning_tokens -> reasoning. Vertex reports it OUTSIDE
      completion_tokens (prompt + completion + reasoning == total_tokens), so it is added
      to billed output. When the three do not add up to the total, reasoning is taken as
      already inside completion_tokens (the OpenAI convention) and not added twice.
"""

import json

from econocontext.engine import make_segment
from econocontext.types import ProviderUsage, Segment, SegmentKind


def text_of(message: dict) -> str:
    """The text a message contributes, plus its tool calls, in a stable form."""
    content = message.get("content")
    if isinstance(content, list):  # content blocks: keep the text parts
        content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    text = content or ""
    if message.get("tool_calls"):
        calls = [{"id": c.get("id"), "name": c.get("function", {}).get("name"),
                  "args": c.get("function", {}).get("arguments")} for c in message["tool_calls"]]
        text += "\n" + json.dumps(calls, sort_keys=True)
    return text


def to_segments(run_id: str, agent_id: str, body: dict) -> tuple[list[Segment], dict[str, int]]:
    """Segments for a request, plus a map from segment id to its index in body['messages']."""
    segments: list[Segment] = []
    index: dict[str, int] = {}
    if body.get("tools"):
        segments.append(make_segment(run_id, agent_id, "tools", SegmentKind.TOOLS,
                                     json.dumps(body["tools"], sort_keys=True), role="system"))
    seen_task = False
    for i, m in enumerate(body.get("messages", [])):
        role, native = m.get("role"), f"pos{i}"
        if role == "system" or role == "developer":
            s = make_segment(run_id, agent_id, native, SegmentKind.SYSTEM, text_of(m), role="system")
        elif role == "tool":
            s = make_segment(run_id, agent_id, m.get("tool_call_id") or native,
                             SegmentKind.TOOL_RESULT, text_of(m), role="tool",
                             pair_id=m.get("tool_call_id"))
        elif role == "assistant" and m.get("tool_calls"):
            ids = ",".join(c.get("id", "") for c in m["tool_calls"])
            s = make_segment(run_id, agent_id, native, SegmentKind.TOOL_CALL, text_of(m),
                             role="assistant", pair_id=ids)
        elif role == "user" and not seen_task:
            seen_task = True
            s = make_segment(run_id, agent_id, native, SegmentKind.TASK, text_of(m), role="user")
        else:
            s = make_segment(run_id, agent_id, native, SegmentKind.MESSAGE, text_of(m),
                             role=role or "user")
        segments.append(s)
        index[s.id] = i
    return segments, index


def from_segments(segments: list[Segment], body: dict, index: dict[str, int]) -> list[dict]:
    """Messages in the rendered order. Originals are reused untouched; retrieved segments
    (not in the request) are appended as plain user context."""
    out = []
    for s in segments:
        if s.kind == SegmentKind.TOOLS:
            continue  # carried by body['tools'], never a message
        if s.id in index:
            out.append(body["messages"][index[s.id]])
        else:
            out.append({"role": "user", "content": f"[Retrieved from earlier context]\n{s.text}"})
    return out


def to_usage(usage: dict | None, latency_ms: float | None = None) -> ProviderUsage:
    if not usage:
        return ProviderUsage(None, None, None, None, latency_ms=latency_ms, raw={})
    prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    details = usage.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    output = completion
    if None not in (prompt, completion, reasoning) and \
            prompt + completion + reasoning == usage.get("total_tokens"):
        output = completion + reasoning  # reasoning reported outside completion_tokens
    uncached = None if prompt is None else prompt - (cached or 0)
    return ProviderUsage(uncached_input=uncached, cache_read=cached, cache_write=None,
                         output=output, reasoning=reasoning, latency_ms=latency_ms,
                         raw=dict(usage))
