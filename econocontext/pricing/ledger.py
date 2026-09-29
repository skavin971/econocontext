"""Actual provider cost per model call and per run.

The ledger is deliberately separate from the planner's cost model. The planner
predicts; this module prices the provider's reported counters using the immutable
price card selected for the call date.
"""

import json
from datetime import date, datetime, timezone

from ..config import PriceCard
from ..store.db import AgentDB
from ..types import ProviderUsage


def _period_name(period: dict) -> str:
    start = period["valid_from"] or "before"
    end = period["valid_until"] or "open"
    return f"{start}..{end}"


def cost(usage: ProviderUsage, card: PriceCard, day: date) -> tuple[float | None, float | None,
                                                                  bool, str]:
    """Return ``(NU, USD, complete, price_period)`` for one provider call.

    Missing billed counters remain unknown. Gemini's implicit cache has no
    separately billed write counter, represented by ``cache_write_applicable=False``.
    """
    period, tier = card.period_and_tier(day, usage.prompt_tokens)
    period_name = _period_name(period)

    required = [usage.uncached_input, usage.cache_read, usage.output]
    if usage.cache_write_applicable:
        required.append(usage.cache_write)
        if tier.cache_write_1h_per_mtok is not None:
            required.append(usage.cache_write_1h)
    if any(value is None for value in required):
        return None, None, False, period_name

    uncached = usage.uncached_input or 0
    cache_read = usage.cache_read or 0
    cache_write = (usage.cache_write or 0) if usage.cache_write_applicable else 0
    cache_write_1h = (usage.cache_write_1h or 0) if usage.cache_write_applicable else 0
    output = usage.output or 0
    input_rate = tier.input_per_mtok
    read_rate = (input_rate if tier.cache_read_per_mtok is None
                 else tier.cache_read_per_mtok)
    write_rate = (input_rate if tier.cache_write_per_mtok is None
                  else tier.cache_write_per_mtok)
    write_1h_rate = (input_rate if tier.cache_write_1h_per_mtok is None
                     else tier.cache_write_1h_per_mtok)

    nu = (uncached + cache_read * read_rate / input_rate
          + cache_write * write_rate / input_rate
          + cache_write_1h * write_1h_rate / input_rate
          + output * tier.output_per_mtok / input_rate)
    usd = (uncached * input_rate + cache_read * read_rate + cache_write * write_rate
           + cache_write_1h * write_1h_rate + output * tier.output_per_mtok) / 1_000_000
    return float(nu), float(usd), True, period_name


class Ledger:
    def __init__(self, db: AgentDB, card: PriceCard):
        self.db, self.card = db, card

    def record(self, outcome_id: str, run_id: str, agent_id: str, decision_id: str | None,
               phase: str, usage: ProviderUsage, on: date | None = None) -> float | None:
        """Save one call's usage and return its known USD cost, if complete."""
        day = on or datetime.now(timezone.utc).date()
        cost_nu, cost_usd, complete, period = cost(usage, self.card, day)
        self.db.add_outcome(outcome_id, run_id, agent_id, decision_id, phase, usage,
                            cost_nu=cost_nu, cost_usd=cost_usd, complete=complete,
                            period=period)
        return cost_usd

    def run_cost(self, run_id: str) -> dict:
        row = self.db.rows(
            "SELECT COALESCE(SUM(cost_usd), 0) AS usd, COALESCE(SUM(cost_nu), 0) AS nu, "
            "SUM(CASE WHEN cost_complete=0 THEN 1 ELSE 0 END) AS incomplete, "
            "COUNT(*) AS calls FROM outcomes WHERE run_id=?", (run_id,))[0]
        incomplete = int(row["incomplete"] or 0)
        return {"cost_usd": float(row["usd"] or 0), "cost_nu": float(row["nu"] or 0),
                "incomplete_calls": incomplete, "calls": int(row["calls"] or 0),
                "complete": incomplete == 0}

    def run_usd(self, run_id: str) -> float:
        """Known USD total. Callers must inspect ``run_cost()['complete']``."""
        return self.run_cost(run_id)["cost_usd"]

    def has_incomplete(self, run_id: str) -> bool:
        return self.run_cost(run_id)["incomplete_calls"] > 0


def summary(db: AgentDB, run_id: str) -> dict:
    """One run's token, cost, timing and decision totals."""
    tot = dict(db.rows(
        "SELECT COUNT(*) AS calls, SUM(uncached_input) AS uncached_input, SUM(cache_read) AS "
        "cache_read, SUM(cache_write) AS cache_write, SUM(output) AS output, SUM(reasoning) AS "
        "reasoning, SUM(cost_nu) AS cost_nu, SUM(cost_usd) AS cost_usd, "
        "SUM(CASE WHEN phase='compaction' THEN 1 ELSE 0 END) AS compaction_calls, "
        "SUM(CASE WHEN cost_complete=0 THEN 1 ELSE 0 END) AS incomplete_calls, "
        "SUM(latency_ms) AS model_latency_ms FROM outcomes WHERE run_id=?", (run_id,))[0])
    prompt = sum(tot[k] or 0 for k in ("uncached_input", "cache_read", "cache_write"))
    tot["cache_read_share"] = (tot["cache_read"] or 0) / prompt if prompt else None
    tot["cost_complete"] = (tot["incomplete_calls"] or 0) == 0

    agents = [dict(r) for r in db.rows(
        "SELECT agent_id, COUNT(*) AS calls, SUM(cost_nu) AS cost_nu, SUM(cost_usd) AS cost_usd "
        "FROM outcomes WHERE run_id=? GROUP BY agent_id", (run_id,))]
    decisions = [dict(r) for r in db.rows(
        "SELECT intercept, chosen, applied, COUNT(*) AS n FROM decisions WHERE run_id=? "
        "GROUP BY intercept, chosen, applied", (run_id,))]
    feasible: dict[str, int] = {}
    for r in db.rows("SELECT feasible FROM decisions WHERE run_id=?", (run_id,)):
        for name in json.loads(r["feasible"]):
            feasible[name] = feasible.get(name, 0) + 1
    pairs = db.rows("SELECT d.predicted_cost, o.cost_nu FROM outcomes o JOIN decisions d ON "
                    "d.decision_id = o.decision_id WHERE o.run_id=?", (run_id,))
    predicted = sum(json.loads(p["predicted_cost"])["prepare"]
                    + json.loads(p["predicted_cost"])["work"] for p in pairs)
    actual = [p["cost_nu"] for p in pairs if p["cost_nu"] is not None]
    run = dict(db.rows("SELECT * FROM runs WHERE run_id=?", (run_id,))[0])
    spans = [dict(r) for r in db.rows(
        "SELECT span_id, agent_id, kind, name, native_id, decision_id, started_at, ended_at, "
        "duration_ms, status, metadata FROM runtime_spans WHERE run_id=? ORDER BY started_at",
        (run_id,))]
    return dict(run=run, totals=tot, agents=agents, decisions=decisions,
                feasible_counts=feasible, spans=spans,
                predicted_vs_actual=dict(calls=len(pairs), predicted_call_nu=predicted,
                                         actual_call_nu=sum(actual) if actual else None))
