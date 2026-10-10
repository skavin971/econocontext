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

  A call that retried an earlier one (span metadata 'retry_of') is skipped: it is the
  same decision again, not another turn.

  estimated_saved_usd (STRICT only) = cost of call t
      - the cache writes of call t, repriced as the next call's writes instead of its
        reads (removing a turn moves its cache write to the next call; it does not
        remove it)
      - evidence tokens x the price of one fresh input token (the evidence is paid once
        more, uncached)
      Conservative for the evidence, optimistic that the next turn would have gone the
      same way. None when call t's cost is unknown (no price card
      for its model); the turn still counts as removable.
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


def schedule(db: AgentDB, run_id: str, prices: dict) -> list[dict]:
    """One row per model call of the run, in order, with its class and (for acquisition
    turns) the evidence that would have had to be in its request. `prices`: USD per token
    for 'input', 'cache_read', 'cache_write' and 'cache_write_1h' (missing = 0)."""
    calls = []
    for r in db.rows("SELECT s.native_id, s.metadata, o.cost_usd, o.cache_write, o.cache_write_1h "
                     "FROM runtime_spans s LEFT JOIN "
                     "outcomes o ON o.outcome_id = s.native_id WHERE s.run_id=? AND s.kind='model' "
                     "ORDER BY s.started_at", (run_id,)):
        meta = json.loads(r["metadata"] or "{}")
        if "call_no" in meta and "retry_of" not in meta:
            calls.append({**meta, "cost_usd": r["cost_usd"], "cache_write": r["cache_write"] or 0,
                          "cache_write_1h": r["cache_write_1h"] or 0})
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
            if kind == STRICT:
                row["removable_calls"] = 1
                read = prices.get("cache_read", 0)
                moved = (call["cache_write"] * (prices.get("cache_write", 0) - read)
                         + call["cache_write_1h"] * (prices.get("cache_write_1h", 0) - read))
                row["estimated_saved_usd"] = (
                    None if call["cost_usd"] is None
                    else call["cost_usd"] - moved - row["tokens_added"] * prices.get("input", 0))
        rows.append(row)
    return rows


def headroom(rows: list[dict]) -> dict:
    """A run's totals: how many calls, and how much cost, the oracle could remove."""
    cost = sum(r["cost_usd"] or 0 for r in rows)
    strict = [r for r in rows if r["class"] == STRICT]
    saved = sum(r["estimated_saved_usd"] or 0 for r in strict)
    return {"calls": len(rows), "strict": len(strict),
            "mixed": sum(r["class"] == MIXED for r in rows),
            "removable_calls": sum(r["removable_calls"] for r in rows),
            "cost_usd": cost, "estimated_saved_usd": saved,
            "strict_unpriced": sum(r["estimated_saved_usd"] is None for r in strict),
            "share_of_calls": len(strict) / len(rows) if rows else None,
            "share_of_cost": saved / cost if cost else None}
