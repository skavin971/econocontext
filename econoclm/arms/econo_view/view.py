"""VIEW.md: the lines that make up the model's next prompt. Pure: no I/O.

Line types (one per line; blank lines and lines starting with "#" are ignored):

  turn K                 a past turn, exactly as CLM appended it: the assistant message
                         (reasoning, tool call, provider fields) and everything that
                         followed it (its tool result, already cut to head and tail by
                         CLM, and any runtime notices)
  obs N                  saved output N, in full
  obs N [lines A-B]      lines A to B of saved output N
  note NAME: TEXT        the model's own note

render() turns a view into the messages after CLM's protected prefix (system + task),
in the view's order. It is a pure function of the view and the stores, so the same view
always gives the same bytes. obs and note lines become user messages. Consecutive
same-role messages are merged at line boundaries (user+user, text-only
assistant+assistant), as CLM's _normalize does, so no provider rejects the request.
A turn's assistant message and its tool result are never split, and a turn always ends
with a non-assistant message, so the prompt never ends with an assistant turn.

With the automatically appended `turn K` lines only (the model never edits the view),
render() gives exactly the messages CLM itself would send.
"""

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Callable

TURN = re.compile(r"^turn\s+(\d+)\s*$")
OBS = re.compile(r"^obs\s+(\d+)\s*(?:\[\s*lines\s+(\d+)\s*-\s*(\d+)\s*\])?\s*$")
NOTE = re.compile(r"^note\s+([^:]+?)\s*:\s?(.*)$")


@dataclass(frozen=True)
class Line:
    kind: str                     # "turn" | "obs" | "note"
    id: int = 0                   # K or N
    a: int | None = None          # obs line range
    b: int | None = None
    name: str = ""                # note name
    text: str = ""                # note text

    def __str__(self) -> str:
        if self.kind == "turn":
            return f"turn {self.id}"
        if self.kind == "obs":
            return f"obs {self.id}" + (f" [lines {self.a}-{self.b}]" if self.a is not None else "")
        return f"note {self.name}: {self.text}"


@dataclass
class Parsed:
    lines: list[Line] = field(default_factory=list)
    bad: list[str] = field(default_factory=list)      # lines that are not valid view lines


def parse(text: str) -> Parsed:
    out = Parsed()
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if m := TURN.match(s):
            out.lines.append(Line("turn", int(m.group(1))))
        elif m := OBS.match(s):
            a, b = m.group(2), m.group(3)
            if a is not None and (int(a) < 1 or int(b) < int(a)):
                out.bad.append(s)
                continue
            out.lines.append(Line("obs", int(m.group(1)), int(a) if a else None, int(b) if b else None))
        elif m := NOTE.match(s):
            out.lines.append(Line("note", name=m.group(1).strip(), text=m.group(2).strip()))
        else:
            out.bad.append(s)
    return out


def dump(lines: list[Line]) -> str:
    return "".join(f"{line}\n" for line in lines)


def fingerprint(message: dict) -> str:
    return json.dumps(message, sort_keys=True, ensure_ascii=False)


def _text(m: dict) -> str | None:
    c = m.get("content")
    return c if isinstance(c, str) else None


def _mergeable(prev: dict, cur: dict) -> bool:
    if prev.get("role") != cur.get("role") or cur.get("role") not in ("user", "assistant"):
        return False
    if prev.get("tool_calls") or cur.get("tool_calls"):
        return False
    return _text(prev) is not None and _text(cur) is not None


def obs_text(text: str, a: int | None, b: int | None) -> str:
    if a is None:
        return text
    return "".join(text.splitlines(keepends=True)[a - 1:b])


@dataclass
class Rendered:
    messages: list[dict]                  # what follows CLM's protected prefix
    starts: list[int]                     # per view line: index of its first message
    missing: list[str]                    # view lines whose turn or obs does not exist


def render(lines: list[Line], turns: dict[int, list[dict]],
           obs: Callable[[int], str | None]) -> Rendered:
    """The messages for these view lines. `turns[K]` is turn K's stored message group;
    `obs(N)` returns saved output N's text (None if there is none)."""
    out: list[dict] = []
    starts: list[int] = []
    missing: list[str] = []
    for line in lines:
        if line.kind == "turn":
            group = turns.get(line.id)
            if not group:
                missing.append(str(line))
                starts.append(len(out))
                continue
            items = [copy.deepcopy(m) for m in group]
        elif line.kind == "obs":
            text = obs(line.id)
            if text is None:
                missing.append(str(line))
                starts.append(len(out))
                continue
            head = f"[obs {line.id}" + (f" lines {line.a}-{line.b}" if line.a is not None else "") + "]"
            items = [{"role": "user", "content": f"{head}\n{obs_text(text, line.a, line.b)}"}]
        else:
            items = [{"role": "user", "content": f"[note {line.name}]\n{line.text}"}]
        first = True
        for m in items:
            if first and out and _mergeable(out[-1], m):
                out[-1] = {"role": m["role"], "content": (_text(out[-1]) + "\n\n" + _text(m)).strip()}
                starts.append(len(out) - 1)
            else:
                if first:
                    starts.append(len(out))
                out.append(m)
            first = False
    return Rendered(out, starts, missing)


def render_bytes(messages: list[dict]) -> bytes:
    """The canonical bytes of a rendered prompt (for the determinism check)."""
    return json.dumps(messages, sort_keys=True, ensure_ascii=False).encode()


def group_new_messages(messages: list[dict]) -> list[list[dict]]:
    """Split messages CLM appended into turns: each turn starts at an assistant message
    and takes every non-assistant message after it; messages before the first assistant
    message (runtime notices) form a turn of their own."""
    groups: list[list[dict]] = []
    for m in messages:
        if m.get("role") == "assistant" or not groups:
            groups.append([m])
        else:
            groups[-1].append(m)
    return groups
