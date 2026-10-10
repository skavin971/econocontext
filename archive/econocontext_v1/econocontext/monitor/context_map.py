"""The worker map: which workers exist, what each already holds, and whether it is free.

Why it exists: before a new sub-task is delegated, the planner can send it to a worker
that already holds the files it needs (RESUME), instead of a fresh one that must read
them again. This builds that picture from the Agent DB, per run:

  title           the name the root gave the worker when it dispatched it
  type            the agent it was dispatched to (e.g. explore, general)
  files           paths the worker read (sys_os_read calls in its own requests)
  lines           the lines it read of each file, at the version it read
                  (file -> [(first, last, version)], from the gateway's evidence events)
  resident_tokens its context size on its last call
  warm            its last call was within the cache lifetime, so its prefix is cached
  busy            a model call of it is open, or it called in the last few seconds

Workers are told apart at the gateway by their first message (their task), which a
continued worker keeps; a dispatch's title is matched to the worker whose task
contains the dispatched text.
What it must never do: write anything.
"""

import json
import os
import re
from datetime import datetime, timezone

from ..store.db import AgentDB, read_paths

BUSY_SECONDS = 5  # a worker that called this recently may be mid-task
LINES = re.compile(r"^L(\d+)-(\d+)$")  # an evidence range: lines first..last


def held_lines(db: AgentDB, run_id: str, agent_id: str | None = None,
               call_nos: list[int] | None = None) -> dict[str, list[tuple[int, int, str]]]:
    """The file lines acquired in a run, by one agent or at some calls:
    file -> [(first, last, version)]."""
    where, args = "e.run_id=? AND e.event='acquired' AND v.source_kind='file'", [run_id]
    if agent_id is not None:
        where += " AND e.agent_id=?"
        args.append(agent_id)
    if call_nos is not None:
        if not call_nos:
            return {}
        where += f" AND e.call_no IN ({','.join('?' * len(call_nos))})"
        args.extend(call_nos)
    lines: dict[str, list[tuple[int, int, str]]] = {}
    for r in db.rows(f"SELECT v.source_key, v.range, v.source_version FROM evidence_events e "
                     f"JOIN evidence v ON v.run_id=e.run_id AND v.evidence_id=e.evidence_id "
                     f"WHERE {where}", tuple(args)):
        span = LINES.match(r["range"] or "")
        held = span and (int(span.group(1)), int(span.group(2)), r["source_version"])
        if held and held not in lines.setdefault(r["source_key"], []):
            lines[r["source_key"]].append(held)
    return {k: v for k, v in lines.items() if v}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def file_tokens(db: AgentDB, run_id: str) -> dict[str, int]:
    """Largest known size, in tokens, of each file read in this run."""
    sizes: dict[str, int] = {}
    for r in db.rows("SELECT t.read_set, s.tokens FROM tool_results t JOIN segments s ON "
                     "s.segment_id = t.result_segment_id WHERE t.run_id=? AND "
                     "t.tool_name='sys_os_read'", (run_id,)):
        for path in json.loads(r["read_set"] or "{}"):
            if path != "*":
                sizes[os.path.normpath(path)] = max(sizes.get(path, 0), r["tokens"])
    return sizes


def workers(db: AgentDB, run_id: str, ttl_seconds: float) -> list[dict]:
    now = datetime.now(timezone.utc)
    dispatched = []
    for r in db.rows("SELECT metadata FROM runtime_spans WHERE run_id=? AND kind='dispatch' "
                     "ORDER BY started_at", (run_id,)):
        meta = json.loads(r["metadata"] or "{}")
        if meta.get("title"):
            dispatched.append((meta["title"], _norm(meta.get("task", "")), meta.get("worker")))
    out = []
    for w in db.rows("SELECT agent_id, MAX(created_at) last, COUNT(*) calls FROM outcomes "
                     "WHERE run_id=? AND agent_id NOT LIKE '%:root' GROUP BY agent_id", (run_id,)):
        agent = w["agent_id"]
        task = db.rows("SELECT text FROM segments WHERE agent_id=? AND kind='task' "
                       "ORDER BY created_at LIMIT 1", (agent,))
        task_text = _norm(task[0]["text"]) if task else ""
        title, kind = next(((t, k) for t, text, k in dispatched if text and text in task_text),
                           (None, None))
        files = set()
        for s in db.rows("SELECT text FROM segments WHERE agent_id=? AND kind='tool_call'", (agent,)):
            files |= read_paths(s["text"])
        last = db.rows("SELECT COALESCE(uncached_input,0)+COALESCE(cache_read,0) p FROM outcomes "
                       "WHERE agent_id=? ORDER BY created_at DESC LIMIT 1", (agent,))[0]["p"]
        idle_for = (now - datetime.fromisoformat(w["last"])).total_seconds()
        open_call = db.rows("SELECT 1 FROM runtime_spans WHERE agent_id=? AND kind='model' "
                            "AND status='open' LIMIT 1", (agent,))
        out.append(dict(worker_id=agent, title=title, type=kind, files=sorted(files),
                        lines=held_lines(db, run_id, agent_id=agent), resident_tokens=last,
                        calls=w["calls"], warm=idle_for < ttl_seconds,
                        busy=bool(open_call) or idle_for < BUSY_SECONDS))
    return out
