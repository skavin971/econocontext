"""One agent session's own database: what the agent has seen, in what form, and every decision.

Why it exists: EconoContext v2 decides inside one session. Nothing from other sessions is
read; a new session starts with an empty file, and the product deletes it at the end (our
experiments archive it). Everything a rule needs is here: each tool result in full (so it
can be served again), whether it is still in the agent's context and in what form, the
write epoch (any write makes stored results stale for serving), and the decision log.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY, call_no INTEGER, agent TEXT, tool TEXT, key TEXT, input TEXT,
  output TEXT, tokens INTEGER, form TEXT, shown_tokens INTEGER, in_context INTEGER DEFAULT 1,
  epoch INTEGER, created REAL);
CREATE INDEX IF NOT EXISTS items_key ON items(key);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY, at REAL, call_no INTEGER, rule TEXT, item_id INTEGER, action TEXT,
  forced INTEGER, prices TEXT, answers TEXT, jev_usage TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
"""

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


class Session:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # state ---------------------------------------------------------------------------
    def get(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, json.dumps(value)))

    def bump(self, key: str, by: int = 1) -> int:
        value = self.get(key, 0) + by
        self.set(key, value)
        return value

    @property
    def epoch(self) -> int:
        return self.get("epoch", 0)

    # items ---------------------------------------------------------------------------
    def add_item(self, agent: str, tool: str, tool_input: dict, output: str, tokens: int,
                 form: str = "full", shown_tokens: int | None = None) -> int:
        cur = self.db.execute(
            "INSERT INTO items (call_no, agent, tool, key, input, output, tokens, form, shown_tokens,"
            " in_context, epoch, created) VALUES (?,?,?,?,?,?,?,?,?,1,?,?)",
            (self.get("calls", 0), agent, tool, tool_key(tool, tool_input), json.dumps(tool_input),
             output, tokens, form, tokens if shown_tokens is None else shown_tokens, self.epoch,
             time.time()))
        return cur.lastrowid

    def item(self, item_id: int):
        return self.db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()

    def last_same(self, agent: str, tool: str, tool_input: dict):
        """The latest earlier result of the identical call by the same agent, or None."""
        return self.db.execute("SELECT * FROM items WHERE agent=? AND key=? ORDER BY id DESC LIMIT 1",
                               (agent, tool_key(tool, tool_input))).fetchone()

    def full_copy_in_context(self, agent: str, tool: str, tool_input: dict):
        """The latest earlier result of the identical call still fully in the agent's context."""
        return self.db.execute(
            "SELECT * FROM items WHERE agent=? AND key=? AND form='full' AND in_context=1 "
            "ORDER BY id DESC LIMIT 1", (agent, tool_key(tool, tool_input))).fetchone()

    def reads_of(self, agent: str, file_path: str):
        """Earlier Read results of this file by this agent, still in context, newest first."""
        return self.db.execute(
            "SELECT * FROM items WHERE agent=? AND tool='Read' AND in_context=1 AND "
            "json_extract(input, '$.file_path')=? ORDER BY id DESC", (agent, file_path)).fetchall()

    def update_item(self, item_id: int, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE items SET {cols} WHERE id=?", (*fields.values(), item_id))

    def live_big(self, agent: str, min_tokens: int):
        return self.db.execute("SELECT * FROM items WHERE agent=? AND in_context=1 AND "
                               "shown_tokens>=? ORDER BY id", (agent, min_tokens)).fetchall()

    def in_context(self, agent: str):
        return self.db.execute("SELECT * FROM items WHERE agent=? AND in_context=1 ORDER BY id",
                               (agent,)).fetchall()

    def drop_from_context(self, agent: str, keep_ids: set[int]) -> int:
        """After a compaction: every item not kept left the agent's context (still stored here)."""
        rows = self.in_context(agent)
        gone = [r["id"] for r in rows if r["id"] not in keep_ids]
        self.db.executemany("UPDATE items SET in_context=0 WHERE id=?", [(i,) for i in gone])
        return len(gone)

    # decisions -----------------------------------------------------------------------
    def log(self, rule: str, action: str, item_id: int | None = None, forced: bool = False,
            prices: dict | None = None, answers: dict | None = None,
            jev_usage: dict | None = None, note: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO decisions (at, call_no, rule, item_id, action, forced, prices, answers,"
            " jev_usage, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (time.time(), self.get("calls", 0), rule, item_id, action, int(forced),
             json.dumps(prices) if prices else None, json.dumps(answers) if answers else None,
             json.dumps(jev_usage) if jev_usage else None, note))

    def decisions(self):
        return self.db.execute("SELECT * FROM decisions ORDER BY id").fetchall()
