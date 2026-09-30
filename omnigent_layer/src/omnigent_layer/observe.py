"""What a model call carried, the same way for every wire format.

Why it exists: the gateway reads tool calls and tool results out of Gemini's and
Anthropic's formats alike. Once each wire (gemini_wire.py, anthropic_wire.py) has put
them in one shape, deciding what a tool did to the workspace, and which evidence
entered the agent's context, is the same code for both.

  a tool result   {"name", "args", "text", "error"}   (error: the tool failed)
  a tool table    {tool name: (kind, argument naming its path)}; kinds:
                    file    reads one file           search  reads the workspace
                    write   changes files ('*' when which ones is unknown)
                    meta    narration, no effect     other   anything else
                  A tool missing from the table is 'other': never a safe read.
What it must never do: read a format, or treat an unknown tool as a read.
"""

import hashlib
import json

from econocontext.evidence import EvidenceEvent, file_version, make_ref, normalize_path, sha256

OTHER = ("other", None)


def args_key(name, args) -> str:
    return hashlib.sha256(json.dumps([name, args], sort_keys=True, default=str).encode()).hexdigest()


def tool_kind(tools: dict, name: str) -> str:
    return tools.get(name, OTHER)[0]


def tool_path(tools: dict, name: str, args: dict) -> str | None:
    """The file a tool call reads or writes, when its arguments name one."""
    key = tools.get(name, OTHER)[1]
    value = (args or {}).get(key) if key else None
    return value if isinstance(value, str) else None


def calls(tools: dict, found: list[dict]) -> list[dict]:
    """Tool calls a reply asked for ([{name, args}]), as recorded: no arguments, only a key."""
    return [{"name": c.get("name"), "kind": tool_kind(tools, c.get("name") or ""),
             "args_key": args_key(c.get("name"), c.get("args") or {})} for c in found]


def evidence_events(results: list[dict], tools: dict, workdir: str | None,
                    epoch: int) -> list[EvidenceEvent]:
    """Tool results new in a request, as evidence events. `epoch` is the run's workspace
    changes so far. A file's version is read from disk when the gateway sees the result;
    a failed read acquires nothing; a write counts as a change even if it failed (the
    safe side)."""
    events = []
    for r in results:
        name, args = r["name"] or "", r["args"] or {}
        kind, key, path = tool_kind(tools, name), args_key(name, args), tool_path(tools, name, args)
        if kind == "write":
            events.append(EvidenceEvent("mutated", name, key,
                                        normalize_path(path, workdir) if path else "*"))
            epoch += 1
            continue
        if kind not in ("file", "search") or r["error"]:
            continue
        text = r["text"]
        if kind == "file" and path:
            source = normalize_path(path, workdir)
            version = file_version(workdir, source)
            narrowed = {k: v for k, v in args.items() if k != tools[name][1]}  # offset, limit...
            ref = make_ref("file", source, version or "text:" + sha256(text), text,
                           json.dumps(narrowed, sort_keys=True) if narrowed else "",
                           recoverable=version is not None)
        else:  # a search, or a multi-file read: depends on the whole workspace
            source = f"{name}:{key}"
            ref = make_ref("search", source, f"epoch:{epoch}", text)
        events.append(EvidenceEvent("acquired", name, key, source, ref))
    return events
