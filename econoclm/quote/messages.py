"""Message-list arithmetic shared by the edit quote and the status line. Pure.

Two facts about CLM decide what an edit costs on the wire:

1. A message "changed" if the provider would see different bytes for it. We compare
   a projection of what is sent: role, content text, tool calls, tool_call_id.
2. CLM's parse_back (context_utils/context_string.py) rebuilds EVERY editable turn
   on an applied edit, and it drops tool structure: an assistant turn with
   tool_calls becomes plain assistant text, a tool turn becomes a user turn. So an
   edit anywhere also rewrites every still-structured turn (one added since the
   previous edit). The real change point of an edit at message i is therefore
   min(i, first structured editable message). first_change() sees this directly
   (it compares before vs after); the status line's depth prices use it.

Token counts come from a `count` function (CLM's tiktoken by default) and are
scaled by `k`, the provider's tokens per our token for the visible text of the same
prompt; hidden.py adds the hidden thinking Gemini also reads, so that positions are in
the provider's units like c is.
"""

import json
import re
from typing import Any, Callable

OBS_TAG = re.compile(r"\[obs (\d+)\]")


def default_count(messages: list[dict]) -> int:
    from clm_harness.utils import tokens as tk
    return tk.count_tokens(messages)[0]


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # OpenAI content parts
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content)


def wire_key(m: dict) -> tuple:
    """What the provider sees of one message."""
    calls = []
    for tc in m.get("tool_calls") or []:
        fn = (tc or {}).get("function") or {}
        calls.append((fn.get("name"), fn.get("arguments") if isinstance(fn.get("arguments"), str)
                      else json.dumps(fn.get("arguments"), sort_keys=True)))
    return (m.get("role"), _text(m.get("content")), tuple(calls), m.get("tool_call_id"))


def first_change(before: list[dict], after: list[dict]) -> int | None:
    """Index of the first message that differs; None if the lists are identical."""
    for i, (a, b) in enumerate(zip(before, after)):
        if wire_key(a) != wire_key(b):
            return i
    if len(before) != len(after):
        return min(len(before), len(after))
    return None


def is_structured(m: dict) -> bool:
    """A turn CLM's parse_back would rewrite on any edit."""
    return m.get("role") == "tool" or bool(m.get("tool_calls"))


def first_structured(messages: list[dict], protect: int) -> int | None:
    for i in range(protect, len(messages)):
        if is_structured(messages[i]):
            return i
    return None


def positions(messages: list[dict], count: Callable[[list[dict]], int]) -> list[int]:
    """positions[i] = tokens before message i; positions[len] = total."""
    out = [0]
    for m in messages:
        out.append(out[-1] + count([m]))
    return out


def obs_ids(messages: list[dict]) -> set[int]:
    """Obs IDs whose [obs N] tag appears anywhere in these messages."""
    found: set[int] = set()
    for m in messages:
        found.update(int(x) for x in OBS_TAG.findall(_text(m.get("content"))))
    return found


def fmt_tokens(n: float) -> str:
    """14234 -> '14.2K', 950 -> '950', 152000 -> '152K'."""
    n = int(round(n))
    if n < 1000:
        return str(n)
    if n < 100_000:
        return f"{n / 1000:.1f}K"
    return f"{n // 1000}K"


def fmt_usd(x: float) -> str:
    return f"${x:.4f}" if x < 0.01 else f"${x:.3f}"
