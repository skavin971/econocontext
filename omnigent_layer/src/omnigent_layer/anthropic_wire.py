"""Anthropic Messages format -> EconoContext types. Translation only.

Why it exists: Claude Code calls the gateway in Anthropic's own format
(POST /v1/messages, usually ?beta=true, usually streamed). This is the single place
that reads that format: whether a request is a model call, what the provider reported,
and what the call carried (sizes, hashes, tool calls and results; never prompt text).
to_segments gives EconoContext's planner the request in segments (observe mode: the
gateway logs the planner's decision and sends the request unchanged).
What it must never do: decide anything, or change a request.

Usage mapping (Anthropic's documented fields):
  usage.input_tokens                 -> uncached input (the part after the last cache read/write)
  usage.cache_read_input_tokens      -> cache_read
  usage.cache_creation.ephemeral_5m_input_tokens -> cache_write (5-minute writes)
  usage.cache_creation.ephemeral_1h_input_tokens -> cache_write_1h
      (without that breakdown, cache_creation_input_tokens is all taken as 5-minute)
  usage.output_tokens                -> output, thinking included (Anthropic bills it as
                                        output and reports no separate count: reasoning
                                        stays unknown)
A stream reports usage in message_start and updates it in message_delta; the later
non-null values win.
"""

import hashlib
import json

from econocontext.engine import make_segment
from econocontext.types import ProviderUsage, Segment, SegmentKind

from . import observe

MESSAGES = "/v1/messages"  # the only model call; count_tokens and the rest pass through

# Claude Code's own tools, by what they do to the workspace (observe.py's kinds).
# testbed_shell is ours, reached through Omnigent's MCP relay.
TOOLS = {
    "Read": ("file", "file_path"),
    "Grep": ("search", None),
    "Glob": ("search", None),
    "LS": ("search", "path"),
    "Edit": ("write", "file_path"),
    "MultiEdit": ("write", "file_path"),
    "Write": ("write", "file_path"),
    "NotebookEdit": ("write", "notebook_path"),
    "Bash": ("write", None),
    "mcp__omnigent__testbed_shell": ("write", None),
    "TodoWrite": ("meta", None),
}


def model_request(rest: str, body: dict) -> tuple[str | None, bool] | None:
    """(model, stream) when the request is a model call; None for anything else."""
    if rest != MESSAGES or not isinstance(body, dict):
        return None
    return body.get("model"), bool(body.get("stream"))


def extract_usage(payloads: list[dict]) -> dict | None:
    """The reply's usage: the body's, or message_start's updated by message_delta's."""
    usage: dict = {}
    for p in payloads:
        if not isinstance(p, dict):
            continue
        found = (p.get("message") or {}).get("usage") if p.get("type") == "message_start" \
            else p.get("usage")
        if isinstance(found, dict):
            usage.update({k: v for k, v in found.items() if v is not None})
    return usage or None


def to_usage(usage: dict | None, latency_ms: float | None = None) -> ProviderUsage:
    if not usage or usage.get("input_tokens") is None:
        return ProviderUsage(None, None, None, None, latency_ms=latency_ms, raw=dict(usage or {}))
    created = usage.get("cache_creation") or {}
    if "ephemeral_5m_input_tokens" in created or "ephemeral_1h_input_tokens" in created:
        write_5m = created.get("ephemeral_5m_input_tokens", 0)
        write_1h = created.get("ephemeral_1h_input_tokens", 0)
    else:
        write_5m, write_1h = usage.get("cache_creation_input_tokens", 0), 0
    return ProviderUsage(uncached_input=usage["input_tokens"],
                         cache_read=usage.get("cache_read_input_tokens", 0),
                         cache_write=write_5m, cache_write_1h=write_1h,
                         output=usage.get("output_tokens"), reasoning=None,
                         latency_ms=latency_ms, raw=dict(usage))


def _blocks(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)]


def _messages(body: dict) -> list[dict]:
    msgs = body.get("messages") if isinstance(body, dict) else None
    return [m for m in msgs if isinstance(m, dict)] if isinstance(msgs, list) else []


def _text(content) -> str:
    return "\n".join(b.get("text", "") for b in _blocks(content) if b.get("type") == "text")


def tool_results(body: dict) -> list[dict]:
    """The tool results new in this request: those in the messages after the last
    assistant turn, each paired with its tool_use by id. observe.py's shape."""
    msgs = _messages(body)
    last = max((i for i, m in enumerate(msgs) if m.get("role") == "assistant"), default=None)
    if last is None:
        return []
    uses = {b.get("id"): b for b in _blocks(msgs[last].get("content")) if b.get("type") == "tool_use"}
    return [{"name": uses.get(b.get("tool_use_id"), {}).get("name"),
             "args": uses.get(b.get("tool_use_id"), {}).get("input") or {},
             "text": _text(b.get("content")), "error": bool(b.get("is_error"))}
            for m in msgs[last + 1:] for b in _blocks(m.get("content"))
            if b.get("type") == "tool_result"]


def reply(payloads: list[dict]) -> tuple[list[dict], bool]:
    """The tool calls a reply asked for ([{name, args}]) and whether it said anything else
    (thinking does not count). A stream is put back together from its content blocks."""
    calls, text, partial = [], False, {}
    for p in payloads:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "message":  # not streamed
            for b in _blocks(p.get("content")):
                if b.get("type") == "tool_use":
                    calls.append({"name": b.get("name"), "args": b.get("input") or {}})
                text = text or (b.get("type") == "text" and bool(b.get("text", "").strip()))
        elif p.get("type") == "content_block_start" and (p.get("content_block") or {}).get("type") == "tool_use":
            partial[p.get("index")] = {"name": p["content_block"].get("name"), "json": ""}
        elif p.get("type") == "content_block_delta":
            delta = p.get("delta") or {}
            if delta.get("type") == "input_json_delta" and p.get("index") in partial:
                partial[p["index"]]["json"] += delta.get("partial_json", "")
            text = text or (delta.get("type") == "text_delta" and bool(delta.get("text", "").strip()))
    for block in partial.values():
        try:
            args = json.loads(block["json"]) if block["json"] else {}
        except ValueError:
            args = {"unparsed": block["json"]}
        calls.append({"name": block["name"], "args": args})
    return calls, text


def context_key(body: dict) -> str:
    """Which conversation a request belongs to: its system prompt and first user message.
    A sub-agent (Claude Code's Task tool) has its own."""
    first = next((m for m in _messages(body) if m.get("role") == "user"), {})
    return hashlib.sha256(json.dumps([body.get("system"), first.get("content")],
                                     sort_keys=True).encode()).hexdigest()[:12]


def observe_request(body: dict, raw: bytes) -> dict:
    """What is recorded about a request (no prompt text)."""
    msgs = _messages(body)
    fixed = {"system": body.get("system"), "tools": body.get("tools"), "messages": msgs[:-1]}
    return {"request_hash": hashlib.sha256(raw).hexdigest(), "request_bytes": len(raw),
            "prefix_hash": hashlib.sha256(json.dumps(fixed, sort_keys=True).encode()).hexdigest(),
            "contents": len(msgs), "context_key": context_key(body),
            "function_responses": [{"name": r["name"], "args_key": observe.args_key(r["name"], r["args"]),
                                    "bytes": len(r["text"].encode())} for r in tool_results(body)]}


def observe_response(payloads: list[dict]) -> dict:
    found, text = reply(payloads)
    return {"response_calls": observe.calls(TOOLS, found), "response_text": text}


def to_segments(run_id: str, agent_id: str, body: dict) -> list[Segment]:
    """Segments for a request, for plan_prompt: the tools, the system prompt, then each
    message. Mid-conversation system messages are SYSTEM; each tool_result block is its
    own TOOL_RESULT, paired with its tool_use by id. Thinking blocks are kept as they are
    (their text is not read). Translation only: nothing here is sent back."""
    segments: list[Segment] = []
    if body.get("tools"):
        segments.append(make_segment(run_id, agent_id, "tools", SegmentKind.TOOLS,
                                     json.dumps(body["tools"], sort_keys=True), role="system"))
    if body.get("system"):
        segments.append(make_segment(run_id, agent_id, "system", SegmentKind.SYSTEM,
                                     _text(body["system"]), role="system"))
    msgs = _messages(body)
    names = {b.get("id"): b.get("name") for m in msgs for b in _blocks(m.get("content"))
             if b.get("type") == "tool_use"}
    seen_task = False
    for i, m in enumerate(msgs):
        role, blocks, native = m.get("role"), _blocks(m.get("content")), f"pos{i}"
        if role == "system":
            segments.append(make_segment(run_id, agent_id, native, SegmentKind.SYSTEM,
                                         _text(blocks), role="system"))
        elif role == "assistant":
            uses = [b for b in blocks if b.get("type") == "tool_use"]
            text = _text(blocks) + ("\n" + json.dumps([{"id": b.get("id"), "name": b.get("name"),
                                                         "input": b.get("input")} for b in uses],
                                                       sort_keys=True) if uses else "")
            segments.append(make_segment(
                run_id, agent_id, native, SegmentKind.TOOL_CALL if uses else SegmentKind.MESSAGE,
                text, role="assistant", pair_id=",".join(b.get("id", "") for b in uses) or None))
        else:
            for b in blocks:
                if b.get("type") == "tool_result":
                    use = b.get("tool_use_id")
                    segments.append(make_segment(run_id, agent_id, use or native,
                                                 SegmentKind.TOOL_RESULT, _text(b.get("content")),
                                                 role="tool", pair_id=use,
                                                 source=f"tool:{names.get(use) or 'unknown'}"))
            text = _text([b for b in blocks if b.get("type") == "text"])
            if text:
                kind = SegmentKind.MESSAGE if seen_task else SegmentKind.TASK
                seen_task = True
                segments.append(make_segment(run_id, agent_id, native, kind, text, role="user"))
    return segments


def with_pointers(body: dict, pointers: dict[str, str]) -> dict:
    """The request with the tool results named in `pointers` (tool_use id -> pointer text)
    replaced by that text: the only change autopilot makes to a Claude Code request.
    Everything else, including thinking blocks, order and cache_control, is kept."""
    if not pointers:
        return body

    def block(b):
        if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in pointers:
            return {**b, "content": [{"type": "text", "text": pointers[b["tool_use_id"]]}]}
        return b

    return {**body, "messages": [
        {**m, "content": [block(b) for b in m["content"]]} if isinstance(m.get("content"), list) else m
        for m in _messages(body)]}


def encode(body: dict) -> bytes:
    """A changed request as bytes, compact as Claude Code sends it."""
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
