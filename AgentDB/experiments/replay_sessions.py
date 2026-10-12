"""Would cross-session memory have served anything? Replay archived Tier A session databases.

For each task that was run several times (r1, r2, r3 ...), walk each later session's tool
calls in order and ask: did an earlier session of the same task make the identical call,
while both sessions were still at write epoch 0 (nothing written yet, so the container is
byte-identical to the task image)? Those are the calls a cross-session memory could have
served without running the tool. Also counted: identical calls at any epoch whose outputs
turned out identical (what content identity could additionally match), and outputs that
differed (what it must refuse).

usage: python replay_sessions.py <sessions dir> [label ...]
No model calls, no network: the sessions are SQLite files written by econocontext/session.py.
"""

import collections
import json
import re
import sqlite3
import sys
from pathlib import Path

READ_ONLY = {"cat", "head", "tail", "ls", "grep", "rg", "find", "wc", "echo", "pwd", "which", "file",
             "stat", "diff", "tree", "du", "df", "env", "printenv", "date", "whoami", "id", "uname",
             "nl", "sort", "uniq", "cut", "tr", "column", "less", "more", "jq"}
READ_ONLY_GIT = {"status", "log", "diff", "show", "branch", "rev-parse", "ls-files", "blame"}
TESTS = re.compile(r"^\s*(python3? -m pytest|pytest|npm test|make test|go test|cargo test)\b")


def read_only_bash(command: str) -> bool:  # econocontext/session.py, verbatim rule
    if any(mark in command for mark in (">", "tee ", " -i", "--in-place", "$(", "`")):
        return False
    for part in command.replace("&&", "|").replace("||", "|").replace(";", "|").split("|"):
        words = part.strip().split()
        if not words:
            continue
        if words[0] == "git" and len(words) > 1 and words[1] in READ_ONLY_GIT:
            continue
        if words[0] == "sed" and "-n" in words:
            continue
        if words[0] not in READ_ONLY:
            return False
    return True


def repeatable(tool: str, args: dict) -> bool:
    command = args.get("command", "")
    return tool == "read_file" or (tool == "bash" and (read_only_bash(command) or bool(TESTS.match(command))))


def calls(path: Path) -> list[dict]:
    """A session's tool calls in order, each with the write epoch *at the time of the call*."""
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    bumps = sorted(r["call_no"] for r in db.execute("SELECT call_no FROM decisions WHERE rule='9 invalidate'"))
    out = []
    for r in db.execute("SELECT id, call_no, tool, input, output, tokens FROM items ORDER BY id"):
        args = json.loads(r["input"] or "{}")
        # Writes before this call: bumps logged at an earlier call, or at this call by an earlier item.
        epoch_before = sum(1 for b in bumps if b < r["call_no"])
        out.append(dict(call_no=r["call_no"], tool=r["tool"], args=args, output=r["output"] or "",
                        tokens=r["tokens"] or 0, epoch=epoch_before,
                        key=json.dumps([r["tool"], {k: v for k, v in args.items() if k != "description"}],
                                       sort_keys=True), repeatable=repeatable(r["tool"], args)))
    return out


def replay(sessions_dir: Path, labels: list[str]) -> None:
    files = sorted(sessions_dir.rglob("*.sqlite3"))
    by_task = collections.defaultdict(list)
    for f in files:
        label = f.parts[-3]
        if labels and label not in labels:
            continue
        m = re.match(r"(.+)-r(\d+)\.sqlite3$", f.name)
        if m:
            by_task[(label, m.group(1))].append((int(m.group(2)), f))
    totals = collections.Counter()
    rows = []
    for (label, task), runs in sorted(by_task.items()):
        runs.sort()
        earlier: dict[str, list[dict]] = collections.defaultdict(list)  # key -> calls in earlier sessions
        for repeat, path in runs:
            cs = calls(path)
            t = collections.Counter()
            for c in cs:
                t["calls"] += 1
                t["tool_tokens"] += c["tokens"]
                if not c["repeatable"]:
                    continue
                t["repeatable"] += 1
                prior = earlier.get(c["key"], [])
                if prior:
                    t["key_hits"] += 1
                    same_out = any(p["output"] == c["output"] for p in prior)
                    if c["epoch"] == 0 and any(p["epoch"] == 0 for p in prior):
                        t["epoch0_hits"] += 1
                        t["epoch0_tokens"] += c["tokens"]
                        t["epoch0_same_output"] += same_out
                    elif same_out:
                        t["later_same_output"] += 1
                        t["later_same_tokens"] += c["tokens"]
                    else:
                        t["later_differs"] += 1
            if repeat > runs[0][0]:
                rows.append((label, task, repeat, t))
                totals.update(t)
            for c in cs:
                earlier[c["key"]].append(c)
    print(f"{'label':<6}{'task':<42}{'rep':>4}{'calls':>6}{'repeat.':>8}{'hits':>6}{'ep0':>5}"
          f"{'ep0=out':>8}{'later=':>7}{'later≠':>7}{'ep0 tok':>9}{'tool tok':>9}")
    for label, task, repeat, t in rows:
        print(f"{label:<6}{task[:40]:<42}{repeat:>4}{t['calls']:>6}{t['repeatable']:>8}{t['key_hits']:>6}"
              f"{t['epoch0_hits']:>5}{t['epoch0_same_output']:>8}{t['later_same_output']:>7}"
              f"{t['later_differs']:>7}{t['epoch0_tokens']:>9}{t['tool_tokens']:>9}")
    t = totals
    print(f"\nover {len(rows)} later sessions of {len(by_task)} tasks:")
    print(f"  tool calls {t['calls']}, repeatable (read-only) {t['repeatable']}, identical to an earlier "
          f"session's call {t['key_hits']}")
    print(f"  servable at epoch 0 (both sessions before any write): {t['epoch0_hits']} calls, "
          f"{t['epoch0_tokens']} of {t['tool_tokens']} tool-output tokens "
          f"({100 * t['epoch0_tokens'] / max(t['tool_tokens'], 1):.1f}%); outputs actually identical in "
          f"{t['epoch0_same_output']} of {t['epoch0_hits']}")
    print(f"  identical calls after writes: outputs identical {t['later_same_output']} "
          f"({t['later_same_tokens']} tokens), outputs differed {t['later_differs']}")


if __name__ == "__main__":
    replay(Path(sys.argv[1]), sys.argv[2:])
