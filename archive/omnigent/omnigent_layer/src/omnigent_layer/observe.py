"""What a model call carried, the same way for every wire format.

Why it exists: the gateway reads tool calls and tool results out of Gemini's and
Anthropic's formats alike. Once each wire (gemini_wire.py, anthropic_wire.py) has put
them in one shape, deciding what a tool did to the workspace, and which evidence
entered the agent's context, is the same code for both.

  a tool result   {"name", "args", "text", "error"}   (error: the tool failed)
  a tool table    {tool name: (kind, argument naming its path[, first line number])}; kinds:
                    file    reads one file           search  reads the workspace
                    write   changes files ('*' when which ones is unknown)
                    shell   runs a command: a read when bash_reads parses it as read-only
                            (sed -n, cat, grep -n ...), else a change to '*'
                    meta    narration, no effect     other   anything else
                  A tool missing from the table is 'other': never a safe read.
                  The third field says whether a read tool's `offset` counts lines from
                  1 (Claude Code) or 0 (Gemini CLI); default 1.
Every file read is held as lines of a version: range 'L<a>-<b>' (econocontext.evidence).
The lines come from the result when it numbers them ('634\tcode', Claude Code's Read),
else from offset/limit, else the whole file.
What it must never do: read a format, or treat an unknown tool as a read.
"""

import hashlib
import json
import os
import re
from pathlib import Path

from econocontext.costmodel import bash_reads
from econocontext.evidence import EvidenceEvent, file_version, make_ref, normalize_path, sha256

OTHER = ("other", None)
NUMBERED = re.compile(r"^\s*(\d+)[\t\u2192]", re.M)  # "634\tcode" or "   634→code"
READ_LIMIT = 2000  # lines a read tool returns without a limit (Claude Code, Gemini CLI)
CONTAINER = "/testbed"  # where benchmark containers mount the workspace


def args_key(name, args) -> str:
    return hashlib.sha256(json.dumps([name, args], sort_keys=True, default=str).encode()).hexdigest()


def tool_kind(tools: dict, name: str) -> str:
    return tools.get(name, OTHER)[0]


def tool_path(tools: dict, name: str, args: dict) -> str | None:
    """The file a tool call reads or writes, when its arguments name one."""
    kind, key = tools.get(name, OTHER)[:2]
    if kind == "shell":  # its argument is a command, not a path
        return None
    value = (args or {}).get(key) if key else None
    return value if isinstance(value, str) else None


def calls(tools: dict, found: list[dict]) -> list[dict]:
    """Tool calls a reply asked for ([{name, args}]), as recorded: no arguments, only a key."""
    return [{"name": c.get("name"), "kind": tool_kind(tools, c.get("name") or ""),
             "args_key": args_key(c.get("name"), c.get("args") or {})} for c in found]


def _lines(workdir: str, source: str) -> list[str] | None:
    try:
        return Path(workdir, source).read_text(errors="replace").splitlines()
    except OSError:
        return None


def _read_range(entry: tuple, args: dict, text: str, n_lines: int) -> tuple[int, int]:
    """The lines a read tool returned."""
    shown = [int(m) for m in NUMBERED.findall(text)]
    if shown:
        return min(shown), max(shown)
    base = entry[2] if len(entry) > 2 else 1
    offset, limit = args.get("offset"), args.get("limit")
    start = (int(offset) + 1 - base) if isinstance(offset, int) and offset >= base else 1
    end = start + (int(limit) if isinstance(limit, int) and limit > 0 else READ_LIMIT) - 1
    return start, (min(end, n_lines) if start <= n_lines else end)


def _held(source: str, version: str, lines: list[str], a: int, b: int, name: str, key: str):
    text = "\n".join(lines[a - 1:b])
    return EvidenceEvent("acquired", name, key, source,
                         make_ref("file", source, version, text, f"L{a}-{b}"))


def shell_events(name: str, key: str, command: str, text: str, workdir: str | None):
    """A shell command's events: the lines it showed, if it only read; else a change."""
    if not workdir:
        return None
    read_only, reads = bash_reads.shell_reads(command, text, workdir, {CONTAINER: workdir})
    if not read_only:
        return None
    by_file: dict[str, list[tuple[int, int]]] = {}
    events = []
    for r in reads:
        source = normalize_path(r.path, workdir)
        lines = _lines(workdir, source) if not os.path.isabs(source) else None
        span = bash_reads.resolve(r, len(lines)) if lines is not None else None
        if span:
            by_file.setdefault(source, []).append(span)
    for source, spans in by_file.items():
        version, lines = file_version(workdir, source), _lines(workdir, source)
        for a, b in bash_reads.merge(spans):
            events.append(_held(source, version, lines, a, b, name, key))
    return events


def evidence_events(results: list[dict], tools: dict, workdir: str | None,
                    epoch: int) -> list[EvidenceEvent]:
    """Tool results new in a request, as evidence events. `epoch` is the run's workspace
    changes so far. A file's version is read from disk when the gateway sees the result;
    a failed read acquires nothing; a write counts as a change even if it failed (the
    safe side), and so does a shell command that is not a plain read."""
    events = []
    for r in results:
        name, args = r["name"] or "", r["args"] or {}
        entry = tools.get(name, OTHER)
        kind, key, path = entry[0], args_key(name, args), tool_path(tools, name, args)
        if kind == "shell":
            command = args.get(entry[1]) if entry[1] else None
            held = shell_events(name, key, command, r["text"], workdir) \
                if isinstance(command, str) and not r["error"] else None
            if held is not None:
                events.extend(held)
                continue
            kind = "write"
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
            lines = _lines(workdir, source) if version else None
            if lines is not None:
                a, b = _read_range(entry, args, text, len(lines))
                events.append(_held(source, version, lines, a, b, name, key))
                continue
            ref = make_ref("file", source, "text:" + sha256(text), text, "", recoverable=False)
        else:  # a search, or a multi-file read: depends on the whole workspace
            source = f"{name}:{key}"
            ref = make_ref("search", source, f"epoch:{epoch}", text)
        events.append(EvidenceEvent("acquired", name, key, source, ref))
    return events
