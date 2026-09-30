"""After a run: which evidence was acquired again, and whether it had to be.

Why it exists: an agent that reads auth.py, edits it and reads it again needed both
reads; one that reads the same bytes twice did not. The evidence events
(econocontext/evidence.py) carry versions, so each acquisition can be labelled:

  arrived_call               the model call whose request carried it
  needed_calls               calls at which the same source was acquired again
  refetched                  1 if it was acquired again at all
  reacquired_same_version    the next reacquisition was at the same version and no
                             change to the source came between: redundant
  reacquired_after_mutation  the source changed before the next reacquisition
                             (a write to its path, or to '*'; any write for a search)

One row per acquisition in `labels` (kind 'evidence', subject = the event's seq).
Derived from these, not stored twice (evidence_summary): was_used_again,
next_use_call, calls_until_next_use, number_future_uses.
What it must never do: change anything but the run's evidence labels.
"""

import json

from ..store.db import AgentDB


def _changed(mutations: list[dict], source_kind: str, source_key: str, after: int,
             before: int) -> bool:
    """Whether a write that can change this source came between two events (by seq)."""
    return any(after < m["seq"] < before and
               (source_kind == "search" or m["source_key"] in (source_key, "*"))
               for m in mutations)


def label_evidence(db: AgentDB, run_id: str) -> dict:
    """Write this run's evidence labels (replacing earlier ones). Returns counts."""
    events = [dict(r) for r in db.rows(
        "SELECT e.seq, e.agent_id, e.call_no, e.event, e.source_key, e.tool_name, "
        "v.source_kind, v.source_version FROM evidence_events e LEFT JOIN evidence v "
        "ON v.run_id = e.run_id AND v.evidence_id = e.evidence_id WHERE e.run_id=? ORDER BY e.seq",
        (run_id,))]
    mutations = [e for e in events if e["event"] == "mutated"]
    acquired = [e for e in events if e["event"] == "acquired"]
    rows, same_total, mutated_total = [], 0, 0
    for i, e in enumerate(acquired):
        later = [x for x in acquired[i + 1:] if x["source_key"] == e["source_key"]]
        same = after_change = None
        if later:
            changed = _changed(mutations, e["source_kind"], e["source_key"], e["seq"], later[0]["seq"])
            same = int(not changed and later[0]["source_version"] == e["source_version"])
            after_change = int(changed or later[0]["source_version"] != e["source_version"])
            same_total += same
            mutated_total += after_change
        rows.append((run_id, "evidence", str(e["seq"]), e["agent_id"], e["tool_name"],
                     e["call_no"], json.dumps(sorted({x["call_no"] for x in later})),
                     int(bool(later)), same, after_change))
    with db.lock:
        db.conn.execute("DELETE FROM labels WHERE run_id=? AND kind='evidence'", (run_id,))
        db.conn.executemany(
            "INSERT INTO labels (run_id, kind, subject_id, agent_id, tool_name, arrived_call, "
            "needed_calls, refetched, reacquired_same_version, reacquired_after_mutation) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
        db.conn.commit()
    return dict(run_id=run_id, acquisitions=len(acquired),
                reacquired=sum(r[7] for r in rows), same_version=same_total,
                after_mutation=mutated_total)


def evidence_summary(db: AgentDB, run_id: str) -> list[dict]:
    """The run's evidence labels, with the derived fields: was_used_again, next_use_call,
    calls_until_next_use, number_future_uses."""
    out = []
    for r in db.rows("SELECT * FROM labels WHERE run_id=? AND kind='evidence' "
                     "ORDER BY CAST(subject_id AS INTEGER)", (run_id,)):
        needed = json.loads(r["needed_calls"] or "[]")
        out.append({**dict(r), "needed_calls": needed, "was_used_again": bool(needed),
                    "next_use_call": needed[0] if needed else None,
                    "calls_until_next_use": needed[0] - r["arrived_call"] if needed else None,
                    "number_future_uses": len(needed)})
    return out
