"""Which tool calls only read, and a call's identity (moved unchanged from session.py in step 12a).

A read-only call may be served again or noted as a repeat; anything else may write, so it bumps the
workspace version.
"""

import hashlib
import json
import re

# Bash commands that only read. Anything else may write, so it bumps the epoch.
READ_ONLY = {"cat", "head", "tail", "ls", "grep", "rg", "find", "wc", "echo", "pwd", "which",
             "file", "stat", "diff", "tree", "du", "df", "env", "printenv", "date", "whoami",
             "id", "uname", "nl", "sort", "uniq", "cut", "tr", "column", "less", "more", "jq"}
READ_ONLY_GIT = {"status", "log", "diff", "show", "branch", "rev-parse", "ls-files", "blame"}


def tool_key(tool: str, tool_input: dict) -> str:
    """Identity of a call: the tool and its arguments, minus Claude Code's free-text description."""
    args = {k: v for k, v in (tool_input or {}).items() if k != "description"}
    return hashlib.sha256(json.dumps([tool, args], sort_keys=True).encode()).hexdigest()[:24]


def read_only_bash(command: str) -> bool:
    """True when every part of a Bash command only reads (conservative: unknown means a write)."""
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


TESTS = re.compile(r"^\s*(python3? -m pytest|pytest|npm test|make test|go test|cargo test)\b")
