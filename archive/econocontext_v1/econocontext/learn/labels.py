"""After a run: the truth the planner only guessed at.

Why it exists: every decision rested on two predictions, H (model calls this agent
will still make) and p (whether a result is needed again). Once a run is over both are
known, so they are written to the `labels` table for replay and for learning.

  decision  h_actual      model calls the deciding agent made after the decision
  result    arrived_call  the agent's call count when the tool result arrived
            needed_calls  call counts at which it was needed again, from two signals:
              refetched   a later call to the same tool with the same arguments, or a
                          later file read of the same path
              referenced  a later assistant message or tool call quotes one of the
                          result's distinctive lines (24+ characters)

Both signals are stored so the definition can be checked. Omnigent sends a worker's tool
events to the root agent's policy, so every result is counted on the root's calls.
What it must never do: change anything but `labels`.
"""

import bisect
import json
import re

from ..store.db import AgentDB, now

MIN_LINE = 24
LINE_NUMBER = re.compile(r"^\s*\d+\s*[\t:|]\s?")
BOILERPLATE = ("exit code:", "stdout:", "stderr:")


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def distinctive_lines(text: str, limit: int = 300) -> list[str]:
    """Lines long enough to show the agent really used this result when quoted later."""
    try:
        parts = _strings(json.loads(text))  # tool results are often JSON with a text field
    except (ValueError, TypeError):
        parts = [text]
    lines = []
    for part in parts:
        for line in part.splitlines():
            line = LINE_NUMBER.sub("", line).strip()
            if len(line) >= MIN_LINE and not line.lower().startswith(BOILERPLATE):
                lines.append(line)
    return sorted(set(lines), key=lines.index)[:limit]


def _calls(db: AgentDB, run_id: str) -> dict[str, list[str]]:
    times: dict[str, list[str]] = {}
    for r in db.rows("SELECT agent_id, created_at FROM outcomes WHERE run_id=? ORDER BY created_at",
                     (run_id,)):
        times.setdefault(r["agent_id"], []).append(r["created_at"])
    return times


def _count(times: list[str], at: str) -> int:
    return bisect.bisect_left(times, at)  # calls strictly before `at`


def _paths(read_set: str | None) -> set[str]:
    return set(json.loads(read_set or "{}")) - {"*"}


def label_run(db: AgentDB, run_id: str) -> dict:
    """Write this run's labels (replacing any earlier ones). Returns counts."""
    times = _calls(db, run_id)
    db.execute("DELETE FROM labels WHERE run_id=?", (run_id,))
    decisions = db.rows("SELECT decision_id, agent_id, created_at FROM decisions WHERE run_id=?",
                        (run_id,))
    for d in decisions:
        agent_times = times.get(d["agent_id"], [])
        db.execute("INSERT INTO labels (run_id, kind, subject_id, agent_id, h_actual) "
                   "VALUES(?,?,?,?,?)", (run_id, "decision", d["decision_id"], d["agent_id"],
                                         len(agent_times) - _count(agent_times, d["created_at"])))

    results = db.rows("SELECT t.tool_result_id, t.agent_id, t.tool_name, t.args_key, t.read_set, "
                      "t.created_at, s.text FROM tool_results t JOIN segments s "
                      "ON s.segment_id = t.result_segment_id WHERE t.run_id=? ORDER BY t.created_at",
                      (run_id,))
    later_output = db.rows("SELECT text, created_at FROM segments WHERE run_id=? AND "
                           "role='assistant' ORDER BY created_at", (run_id,))
    needed_total = 0
    for i, r in enumerate(results):
        agent_times = times.get(r["agent_id"], [])
        refetch_at = [x["created_at"] for x in results[i + 1:]
                      if x["tool_name"] == r["tool_name"] and
                      (x["args_key"] == r["args_key"] or
                       (r["tool_name"] == "sys_os_read" and _paths(x["read_set"]) & _paths(r["read_set"])))]
        lines = distinctive_lines(r["text"])
        quote_at = [o["created_at"] for o in later_output if o["created_at"] > r["created_at"]
                    and any(line in o["text"] for line in lines)]
        needed = sorted({_count(agent_times, t) for t in refetch_at + quote_at})
        needed_total += bool(needed)
        db.execute("INSERT INTO labels (run_id, kind, subject_id, agent_id, tool_name, arrived_call, "
                   "needed_calls, refetched, referenced) VALUES(?,?,?,?,?,?,?,?,?)",
                   (run_id, "result", r["tool_result_id"], r["agent_id"], r["tool_name"],
                    _count(agent_times, r["created_at"]), json.dumps(needed),
                    int(bool(refetch_at)), int(bool(quote_at))))
    return dict(run_id=run_id, decisions=len(decisions), results=len(results),
                needed_again=needed_total, labelled_at=now())
