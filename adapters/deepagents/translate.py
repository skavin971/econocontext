"""LangChain messages <-> EconoContext Segments. Translation only.

Why it exists: the core speaks Segments; LangChain speaks messages. This is the
single place that converts between them, so identity (and therefore caching and
reuse) is computed the same way everywhere.
What it must never do: decide anything, or change a message the plan did not
change. When the request is used unchanged, the original message objects are sent.
"""

import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from econocontext.engine import make_segment
from econocontext.types import Representation, Segment, SegmentKind


def message_text(message: BaseMessage) -> str:
    """The text a message contributes, plus its tool calls, in a stable form."""
    text = message.text if isinstance(message.text, str) else str(message.content)
    if isinstance(message, AIMessage) and message.tool_calls:
        calls = [{"id": c["id"], "name": c["name"], "args": c["args"]} for c in message.tool_calls]
        text = text + "\n" + json.dumps(calls, sort_keys=True, default=str)
    return text


def tools_text(tools: list[Any]) -> str:
    """Tool schemas as stable text, so the FROZEN zone has a size and a hash."""
    rendered = []
    for tool in tools or []:
        if isinstance(tool, dict):
            rendered.append(tool)
        else:
            schema = getattr(tool, "args", None)
            rendered.append({"name": getattr(tool, "name", str(tool)),
                             "description": getattr(tool, "description", ""), "args": schema})
    return json.dumps(rendered, sort_keys=True, default=str)


def to_segments(run_id: str, agent_id: str, system: SystemMessage | None, tools: list[Any],
                messages: list[BaseMessage]) -> tuple[list[Segment], dict[str, BaseMessage]]:
    """Segments for a model request, plus a map from segment id to the original message."""
    segments: list[Segment] = []
    originals: dict[str, BaseMessage] = {}
    if system is not None:
        segments.append(make_segment(run_id, agent_id, "system", SegmentKind.SYSTEM,
                                     message_text(system), role="system"))
    if tools:
        segments.append(make_segment(run_id, agent_id, "tools", SegmentKind.TOOLS,
                                     tools_text(tools), role="system"))
    seen_task = False
    for i, m in enumerate(messages):
        native = m.id or f"pos{i}"
        if isinstance(m, ToolMessage):
            kind = SegmentKind.SUBAGENT_RESULT if m.name == "task" else SegmentKind.TOOL_RESULT
            s = make_segment(run_id, agent_id, m.tool_call_id, kind, message_text(m), role="tool",
                             pair_id=m.tool_call_id)
        elif isinstance(m, AIMessage):
            ids = ",".join(c["id"] for c in m.tool_calls) if m.tool_calls else None
            kind = SegmentKind.TOOL_CALL if ids else SegmentKind.MESSAGE
            s = make_segment(run_id, agent_id, native, kind, message_text(m), role="assistant",
                             pair_id=ids)
        elif isinstance(m, HumanMessage) and not seen_task:
            seen_task = True
            s = make_segment(run_id, agent_id, native, SegmentKind.TASK, message_text(m),
                             role="user")
        else:
            role = "system" if isinstance(m, SystemMessage) else "user"
            s = make_segment(run_id, agent_id, native, SegmentKind.MESSAGE, message_text(m),
                             role=role)
        segments.append(s)
        originals[s.id] = m
    return segments, originals


def from_segments(segments: list[Segment], originals: dict[str, BaseMessage],
                  pointer_texts: dict[str, str] | None = None) -> list[BaseMessage]:
    """Messages for the request, in the rendered order (system and tools stay separate)."""
    out: list[BaseMessage] = []
    for s in segments:
        if s.kind in (SegmentKind.SYSTEM, SegmentKind.TOOLS):
            continue  # carried by the request's system_message and tools, never re-sent here
        original = originals.get(s.id)
        if original is None:
            # Retrieved from the store: appended at the tail as plain context.
            out.append(HumanMessage(content=f"[Retrieved from earlier context]\n{s.text}"))
        elif s.representation == Representation.POINTER and pointer_texts and s.id in pointer_texts:
            out.append(original.model_copy(update={"content": pointer_texts[s.id]}))
        else:
            out.append(original)
    return out
