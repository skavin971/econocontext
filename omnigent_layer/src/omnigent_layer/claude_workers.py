"""Claude Code's sub-agents, as the gateway sees them: who exists, what each holds, who is idle.

Why it exists: worker placement needs to know, when Claude Code asks for a new sub-agent,
which of its earlier ones could take the task instead. Claude Code starts a sub-agent with
the Agent tool (its result says "agentId: <id> ... Use SendMessage with to: '<id>' ... to
continue this agent"), the sub-agent works in its own conversation, and it reports back
with SubagentHandback. All of that passes the gateway, so it is read there:

  a worker        an Agent call and the agent id in its result (the root's history)
  its loop        the conversation whose first user message is the Agent call's prompt
                  (context_key, as for every call)
  idle            its latest reply was a SubagentHandback (no tool calls after it)
  what it holds   the evidence its loop acquired (files, searches), and its context size

Nothing here decides anything: policy.py asks for the workers when an Agent call comes.
Prompt text is not stored; only its hash (to match the sub-agent's loop).
"""

import hashlib
import json
import re
from datetime import datetime, timezone

from econocontext.store.db import AgentDB, now

TABLE = ("CREATE TABLE IF NOT EXISTS claude_workers (run_id TEXT, agent_id TEXT, "
         "subagent_type TEXT, description TEXT, prompt_hash TEXT, context_key TEXT, "
         "started_at TEXT, last_call_at TEXT, status TEXT, resumed INTEGER DEFAULT 0, "
         "PRIMARY KEY (run_id, agent_id))")
AGENT_ID = re.compile(r"agentId:\s*([A-Za-z0-9_-]+)")
HANDBACK = "SubagentHandback"


def _hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


def _texts(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    return [b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]


def _result_text(content) -> str:
    return "\n".join(_texts(content))


def track_request(db: AgentDB, run_id: str, body: dict, context_key: str) -> None:
    """From a request: register workers the root just started (an Agent call whose result
    carries an id), mark workers the root just continued (SendMessage), and tie a
    sub-agent's loop to its worker by its first user message. Only the tool results new
    in this request are read, so each event counts once."""
    db.execute(TABLE)
    msgs = [m for m in body.get("messages") or [] if isinstance(m, dict)]
    last = max((i for i, m in enumerate(msgs) if m.get("role") == "assistant"), default=None)
    if last is not None and isinstance(msgs[last].get("content"), list):
        uses = {b.get("id"): b for b in msgs[last]["content"]
                if isinstance(b, dict) and b.get("type") == "tool_use"}
        for m in msgs[last + 1:]:
            for b in m.get("content") if isinstance(m.get("content"), list) else []:
                if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                    continue
                use = uses.get(b.get("tool_use_id")) or {}
                args = use.get("input") or {}
                if use.get("name") == "Agent":
                    found = AGENT_ID.search(_result_text(b.get("content")))
                    if found and args.get("prompt"):
                        db.execute("INSERT OR IGNORE INTO claude_workers (run_id, agent_id, "
                                   "subagent_type, description, prompt_hash, started_at, status) "
                                   "VALUES(?,?,?,?,?,?,?)",
                                   (run_id, found.group(1), args.get("subagent_type") or "general-purpose",
                                    args.get("description"), _hash(args["prompt"]), now(), "running"))
                elif use.get("name") == "SendMessage" and args.get("to") and not b.get("is_error"):
                    db.execute("UPDATE claude_workers SET status='running', resumed = resumed + 1 "
                               "WHERE run_id=? AND agent_id=?", (run_id, args["to"]))
    first = next((m for m in msgs if m.get("role") == "user"), None)
    if first is not None:  # a sub-agent loop's first user message is its task
        for text in _texts(first.get("content")):
            db.execute("UPDATE claude_workers SET context_key=? WHERE run_id=? AND prompt_hash=? "
                       "AND context_key IS NULL", (context_key, run_id, _hash(text)))


def track_reply(db: AgentDB, run_id: str, context_key: str, response_calls: list[dict]) -> None:
    """After a reply in a worker's loop: when it last worked, and whether it handed back."""
    db.execute(TABLE)
    idle = any(c.get("name") == HANDBACK for c in response_calls)
    db.execute("UPDATE claude_workers SET last_call_at=?, status=? WHERE run_id=? AND context_key=?",
               (now(), "idle" if idle else "running", run_id, context_key))


def holdings(db: AgentDB, run_id: str, context_key: str) -> dict:
    """What a worker's loop already has: files and searches acquired (with tokens), its
    calls, and the context it last sent (the prefix a continuation would read)."""
    calls = [n for (n,) in db.rows(
        "SELECT json_extract(metadata, '$.call_no') FROM runtime_spans WHERE run_id=? AND "
        "kind='model' AND json_extract(metadata, '$.context_key')=?", (run_id, context_key))]
    files: dict[str, int] = {}
    if calls:
        marks = ",".join("?" * len(calls))
        for r in db.rows(f"SELECT v.source_key, v.source_kind, v.token_size FROM evidence_events e "
                         f"JOIN evidence v ON v.run_id=e.run_id AND v.evidence_id=e.evidence_id "
                         f"WHERE e.run_id=? AND e.event='acquired' AND e.call_no IN ({marks})",
                         (run_id, *calls)):
            if r["source_kind"] == "file":
                files[r["source_key"]] = max(files.get(r["source_key"], 0), r["token_size"])
    last = db.rows("SELECT COALESCE(o.uncached_input,0)+COALESCE(o.cache_read,0)+COALESCE(o.cache_write,0) p "
                   "FROM runtime_spans s JOIN outcomes o ON o.outcome_id=s.native_id WHERE s.run_id=? AND "
                   "json_extract(s.metadata, '$.context_key')=? ORDER BY s.started_at DESC LIMIT 1",
                   (run_id, context_key))
    return {"files": files, "calls": len(calls), "context_tokens": last[0]["p"] if last else 0}


def workers(db: AgentDB, run_id: str, ttl_seconds: float) -> list[dict]:
    """The run's workers in the form planner.for_placement prices (title = agent id)."""
    db.execute(TABLE)
    out = []
    for w in db.rows("SELECT * FROM claude_workers WHERE run_id=? AND context_key IS NOT NULL",
                     (run_id,)):
        held = holdings(db, run_id, w["context_key"])
        idle_for = ((datetime.now(timezone.utc) - datetime.fromisoformat(w["last_call_at"])).total_seconds()
                    if w["last_call_at"] else None)
        out.append(dict(worker_id=w["agent_id"], title=w["agent_id"], type=w["subagent_type"],
                        description=w["description"], files=sorted(held["files"]),
                        file_tokens=held["files"], resident_tokens=held["context_tokens"],
                        calls=held["calls"], busy=w["status"] != "idle",
                        warm=idle_for is not None and idle_for < ttl_seconds))
    return out


def report(db: AgentDB, run_id: str) -> list[dict]:
    """Per worker: its loop's calls, tokens and cost, and what it read."""
    db.execute(TABLE)
    out = []
    for w in db.rows("SELECT * FROM claude_workers WHERE run_id=? ORDER BY started_at", (run_id,)):
        tot = db.rows("SELECT COUNT(*) calls, SUM(o.uncached_input) fresh, SUM(o.cache_read) cached, "
                      "SUM(o.cache_write) written, SUM(o.cost_usd) usd FROM runtime_spans s JOIN outcomes o "
                      "ON o.outcome_id=s.native_id WHERE s.run_id=? AND "
                      "json_extract(s.metadata, '$.context_key')=?", (run_id, w["context_key"]))[0]
        held = holdings(db, run_id, w["context_key"]) if w["context_key"] else {"files": {}}
        out.append({"agent_id": w["agent_id"], "type": w["subagent_type"],
                    "description": w["description"], "resumed": w["resumed"], "status": w["status"],
                    **dict(tot), "files_read": sorted(held["files"])})
    return out


def as_json(rows: list[dict]) -> str:
    return json.dumps(rows, indent=1, default=str)
