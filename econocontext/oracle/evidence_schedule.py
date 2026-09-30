"""The perfect-future oracle: how many model turns only fetched evidence?

Why it exists: before building anything that supplies evidence early, measure the
most it could remove. With the future known, a model turn whose only decision was to
read (files, searches) could have been skipped if that evidence had already been in
its request: the next turn would have come one call sooner. This is an upper bound,
computed offline from a finished run; nothing here predicts or changes anything.

For each model call t, from what its reply asked for (span metadata 'response_calls',
'response_text', written by the gateway) and the evidence the next call of the same
conversation received (evidence_events):

  STRICT_ACQUISITION           only safe reads (file, search), no text. Narration
                               ('meta', e.g. Gemini's update_topic) does not count.
  MIXED_REASONING_ACQUISITION  safe reads plus text: the turn also said something
  NOT_REMOVABLE                anything else: writes, shell, sub-agents, web, a final
                               answer. Never claimed.

  estimated_saved_usd (STRICT only) = cost of call t - evidence tokens x the price of
      one fresh input token: the turn is gone, and its evidence is paid once more as
      uncached input. Conservative for the evidence, optimistic that the next turn
      would have gone the same way.
What it must never do: count a turn with a side effect, or read the future into a
prediction (it is a bound, not a policy).
"""

import json

from ..store.db import AgentDB

STRICT, MIXED, NOT_REMOVABLE = "STRICT_ACQUISITION", "MIXED_REASONING_ACQUISITION", "NOT_REMOVABLE"
READS = ("file", "search")


def classify(response_calls: list[dict], response_text: bool) -> str:
    calls = [c for c in response_calls if c.get("kind") != "meta"]
    if not calls or any(c.get("kind") not in READS for c in calls):
        return NOT_REMOVABLE
    return MIXED if response_text else STRICT


def schedule(db: AgentDB, run_id: str, usd_per_input_token: float) -> list[dict]:
    """One row per model call of the run, in order, with its class and (for acquisition
    turns) the evidence that would have had to be in its request."""
    calls = []
    for r in db.rows("SELECT s.native_id, s.metadata, o.cost_usd FROM runtime_spans s LEFT JOIN "
                     "outcomes o ON o.outcome_id = s.native_id WHERE s.run_id=? AND s.kind='model' "
                     "ORDER BY s.started_at", (run_id,)):
        meta = json.loads(r["metadata"] or "{}")
        if "call_no" in meta:
            calls.append({**meta, "cost_usd": r["cost_usd"]})
    evidence: dict[int, list[dict]] = {}
    for r in db.rows("SELECT e.call_no, e.evidence_id, v.source_key, v.source_version, v.token_size "
                     "FROM evidence_events e JOIN evidence v ON v.run_id = e.run_id AND "
                     "v.evidence_id = e.evidence_id WHERE e.run_id=? AND e.event='acquired' "
                     "ORDER BY e.seq", (run_id,)):
        evidence.setdefault(r["call_no"], []).append(dict(r))
    rows = []
    for i, call in enumerate(calls):
        kind = classify(call.get("response_calls") or [], bool(call.get("response_text")))
        consumer = next((c for c in calls[i + 1:] if c.get("context_key") == call.get("context_key")),
                        None)
        row = {"call": call["call_no"], "class": kind, "cost_usd": call["cost_usd"],
               "consumer": consumer["call_no"] if consumer else None, "evidence": [],
               "tokens_added": 0, "removable_calls": 0, "estimated_saved_usd": 0.0}
        if kind != NOT_REMOVABLE and consumer is not None:
            row["evidence"] = [{k: e[k] for k in ("evidence_id", "source_key", "source_version")}
                               for e in evidence.get(consumer["call_no"], [])]
            row["tokens_added"] = sum(e["token_size"] for e in evidence.get(consumer["call_no"], []))
            if kind == STRICT and call["cost_usd"] is not None:
                row["removable_calls"] = 1
                row["estimated_saved_usd"] = call["cost_usd"] - row["tokens_added"] * usd_per_input_token
        rows.append(row)
    return rows


def headroom(rows: list[dict]) -> dict:
    """A run's totals: how many calls, and how much cost, the oracle could remove."""
    cost = sum(r["cost_usd"] or 0 for r in rows)
    strict = [r for r in rows if r["class"] == STRICT]
    saved = sum(r["estimated_saved_usd"] for r in strict)
    return {"calls": len(rows), "strict": len(strict),
            "mixed": sum(r["class"] == MIXED for r in rows),
            "removable_calls": sum(r["removable_calls"] for r in rows),
            "cost_usd": cost, "estimated_saved_usd": saved,
            "share_of_calls": len(strict) / len(rows) if rows else None,
            "share_of_cost": saved / cost if cost else None}
