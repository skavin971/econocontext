"""Actual provider cost per model call and per run.

The ledger is deliberately separate from the planner's cost model. The planner
predicts; this module prices the provider's reported counters using the immutable
price card selected for the call date.
"""

import json
from datetime import date, datetime, timezone

from ..config import PriceCard
from ..costmodel.meter import price_call
from ..store.db import AgentDB
from ..types import ProviderUsage
from .rates import from_tier


def _period_name(period: dict) -> str:
    start = period["valid_from"] or "before"
    end = period["valid_until"] or "open"
    return f"{start}..{end}"


def cost(usage: ProviderUsage, card: PriceCard, day: date) -> tuple[float | None, float | None,
                                                                  bool, str]:
    """Return ``(NU, USD, complete, price_period)`` for one provider call.

    Missing billed counters remain unknown. Gemini's implicit cache has no
    separately billed write counter, represented by ``cache_write_applicable=False``.
    The price itself is costmodel.meter.price_call: the same formula the planner uses.
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

    rates = from_tier(card.model, tier)
    nu = price_call(usage, rates)
    usd = nu * rates.usd_per_nu
    return float(nu), float(usd), True, period_name


class Ledger:
    def __init__(self, db: AgentDB, card: PriceCard,
                 cards: dict[tuple[str, str], PriceCard] | None = None):
        self.db, self.card, self.cards = db, card, cards or {}

    def card_for(self, model: str | None) -> PriceCard | None:
        """The card of the model a call actually used (a harness may call a lighter model
        for routing). None when that model has no card: its cost is then unknown, never
        priced as the run's model."""
        name = (model or self.card.model).split("/")[-1]
        if name == self.card.model:
            return self.card
        return self.cards.get((self.card.provider, name))

    def record(self, outcome_id: str, run_id: str, agent_id: str, decision_id: str | None,
               phase: str, usage: ProviderUsage, on: date | None = None,
               model: str | None = None) -> float | None:
        """Save one call's usage and return its known USD cost, if complete."""
        day = on or datetime.now(timezone.utc).date()
        card = self.card_for(model)
        cost_nu, cost_usd, complete, period = (cost(usage, card, day) if card
                                               else (None, None, False, None))
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
