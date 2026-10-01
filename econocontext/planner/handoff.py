"""HANDOFF's brief: the lines an idle worker already read, given to a new worker.

Why it exists: resuming a worker sends its whole history on every call (wres4: 30K
tokens, of which the follow-up needed 8%); a new worker re-reads what it needs. A
handoff sits between: a new worker, plus the lines the idle worker read, so it does not
have to read them again, without the rest of that history.

  which lines   the worker's held ranges (file, first..last), merged; files the task
                names first, then the rest in the order they were read; until
                `max_tokens`
  which text    the lines as they are NOW in the workspace (a worker's reads may predate
                an edit: the brief is never stale), each labelled with its path and lines
What it must never do: write anything, or include a file outside the workspace.
"""

import os
from pathlib import Path

from ..costmodel.bash_reads import merge
from ..tokens import count_tokens

HEADER = ("Context from an earlier worker in this session: lines it already read, as they are "
          "in the workspace now. Read more only if you need it.")


def brief(workdir: str, lines: dict[str, list[tuple]], task: str, max_tokens: int) -> str:
    """The brief's text ('' when nothing fits or nothing is held)."""
    root = os.path.realpath(workdir)
    named = [p for p in lines if p in task or os.path.basename(p) in task]
    parts, used = [HEADER], count_tokens(HEADER)
    for path in named + [p for p in lines if p not in named]:
        full = os.path.realpath(os.path.join(root, path))
        if not full.startswith(root + os.sep):
            continue
        try:
            text = Path(full).read_text(errors="replace").splitlines()
        except OSError:
            continue
        for a, b in merge([(r[0], r[1]) for r in lines[path]]):
            body = "\n".join(text[a - 1:b])
            if not body:
                continue
            part = f"{path} lines {a}-{min(b, len(text))}:\n```\n{body}\n```"
            size = count_tokens(part)
            if used + size > max_tokens:
                continue
            parts.append(part)
            used += size
    return "\n\n".join(parts) if len(parts) > 1 else ""
