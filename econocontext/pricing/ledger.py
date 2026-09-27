"""Ledger: actual cost per model call and per run. Cost is not built yet.

Today `Ledger.record` saves each call's reported usage to `outcomes` with the cost
columns NULL, and `summary` / `run_usd` sum whatever is there. The planner's
cost_model.py *predicts* cost; this module *measures* it, and `summary` joins the
two (predicted vs actual per call).

To build
    `cost(usage, card, day) -> (cost_nu, cost_usd, complete, price_period)`, then
    call it in `Ledger.record` and write the four `outcomes` columns (cost_nu,
    cost_usd, cost_complete, price_period; see store/schema.sql). Return USD from
    `record`: the budget guard in adapters/deepagents/callbacks.py reads
    `run_usd`, so dollar budgets are inert until this is done (only the step
    limit caps a run).

Inputs
    usage   types.ProviderUsage: uncached_input, cache_read, cache_write, output
            (includes reasoning; do not add it twice). None = not reported, never 0;
            a None billed counter makes the cost incomplete. Gemini reports no
            cache_write (implicit cache), so None there is expected, not missing.
    card    config.PriceCard from config/billing_rates.yaml (already on self.card);
            card.tier(day, usage.prompt_tokens) picks the price period and tier.
            USD per 1M tokens. pricing/rates.py:from_tier gives the NU ratios
            (1 NU = one uncached input token of the model in use).

Check
    tests/unit/test_ledger.py (skipped until cost() exists): a real v0 bill,
    151,528 uncached + 318,700 cached + 17,434 output = $0.202926 at promo rates;
    the same usage costs 2x after 2026-12-31; a missing counter is incomplete.
    Runs recorded before this was built need a backfill over `outcomes`.

Out of scope: Jev decider calls, cache storage, compute.
"""

import json
from datetime import date, datetime, timezone

from ..config import PriceCard
from ..store.db import AgentDB
from ..types import ProviderUsage


def cost(usage: ProviderUsage, card: PriceCard, day: date) -> tuple[float, float, bool, str]:
    """Return (cost_nu, cost_usd, complete, price_period) for one call."""
    # PLACEHOLDER: not built. See "To build" in the module docstring.
    raise NotImplementedError("ledger.cost is not built yet: see econocontext/pricing/ledger.py")


class Ledger:
    def __init__(self, db: AgentDB, card: PriceCard):
        self.db, self.card = db, card

    def record(self, outcome_id: str, run_id: str, agent_id: str, decision_id: str | None,
               phase: str, usage: ProviderUsage, on: date | None = None) -> float:
        """Save one call's usage. Returns its USD cost (0.0 until cost() is built)."""
        day = on or datetime.now(timezone.utc).date()  # the date picks the price period
        # PLACEHOLDER: fill the cost columns from cost(usage, self.card, day).
        self.db.add_outcome(outcome_id, run_id, agent_id, decision_id, phase, usage,
                            cost_nu=None, cost_usd=None, complete=False, period=None)
        return 0.0

    def run_usd(self, run_id: str) -> float:
        """Total USD of a run so far (a SQL sum; 0 while cost columns are empty)."""
        rows = self.db.rows("SELECT COALESCE(SUM(cost_usd), 0) AS usd FROM outcomes "
                            "WHERE run_id=?", (run_id,))
        return float(rows[0]["usd"])


def summary(db: AgentDB, run_id: str) -> dict:
    """One run's token totals, cost totals (None until built) and decision counts."""
    tot = dict(db.rows(
        "SELECT COUNT(*) AS calls, SUM(uncached_input) AS uncached_input, SUM(cache_read) AS "
        "cache_read, SUM(cache_write) AS cache_write, SUM(output) AS output, SUM(reasoning) AS "
        "reasoning, SUM(cost_nu) AS cost_nu, SUM(cost_usd) AS cost_usd, "
        "SUM(CASE WHEN phase='compaction' THEN 1 ELSE 0 END) AS compaction_calls "
        "FROM outcomes WHERE run_id=?", (run_id,))[0])
    prompt = sum(tot[k] or 0 for k in ("uncached_input", "cache_read", "cache_write"))
    tot["cache_read_share"] = (tot["cache_read"] or 0) / prompt if prompt else None
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
    # Predicted (cost model) vs actual (this ledger) for calls that had a plan_prompt decision.
    pairs = db.rows("SELECT d.predicted_cost, o.cost_nu FROM outcomes o JOIN decisions d ON "
                    "d.decision_id = o.decision_id WHERE o.run_id=?", (run_id,))
    predicted = sum(json.loads(p["predicted_cost"])["prepare"]
                    + json.loads(p["predicted_cost"])["work"] for p in pairs)
    actual = [p["cost_nu"] for p in pairs if p["cost_nu"] is not None]
    return dict(run=dict(db.rows("SELECT * FROM runs WHERE run_id=?", (run_id,))[0]),
                totals=tot, agents=agents, decisions=decisions, feasible_counts=feasible,
                predicted_vs_actual=dict(calls=len(pairs), predicted_call_nu=predicted,
                                         actual_call_nu=sum(actual) if actual else None))
