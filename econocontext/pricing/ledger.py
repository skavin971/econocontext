"""The ledger: what each model call actually cost, and the total cost of a run.

STATUS: a skeleton. It saves every call's token counts to the `outcomes` table.
It does not compute cost yet. That is yours to build; the steps are below.

Why it exists: this is how results are measured. The planner's cost model
(cost_model.py) only *predicts* cost; the ledger records what the provider
actually billed, so predicted and actual can be compared.
What it must never do: treat a counter the provider did not report as zero.

------------------------------------------------------------------------------
HOW TO BUILD IT (about 40 lines of code)
------------------------------------------------------------------------------

What you get for every model call (a `ProviderUsage`, see types.py):
    uncached_input   input tokens that were NOT served from the cache
    cache_read       input tokens served from the cache (cheaper)
    cache_write      tokens written to a cache (None for Gemini: no separate charge)
    output           output tokens, INCLUDING reasoning (never add reasoning again)
    None             means "the provider did not report it", not zero

Where the prices are: config/billing_rates.yaml, already loaded for you as
`self.card` (a `PriceCard`, see config.py). `self.card.tier(day, usage.prompt_tokens)`
returns the right `PriceTier` for the call's date and prompt size. Prices are USD
per 1M tokens. Gemini 3.6 Flash, until 2026-12-31: input 0.75, cache read 0.075,
output 3.75. From 2027-01-01 each is doubled.

Step 1. Write `cost(usage, card, day)` below:
          usd = (uncached_input x input + cache_read x cache_read_price
                 + cache_write x cache_write_price + output x output_price) / 1_000_000
        NU (normalized units) = usd divided by the price of ONE uncached input
        token, i.e. usd / (tier.input_per_mtok / 1_000_000).
        `pricing/rates.py:from_tier` already gives you these ratios; reuse it.
        Set complete=False if a billed counter is None (cache_write may be None).
        Return (nu, usd, complete, period_label), e.g. period "promo-2026".

Step 2. In `Ledger.record`, call `cost(...)` and pass its results to
        `self.db.add_outcome(...)` instead of the None values. These are the DB
        columns you fill (store/schema.sql, table `outcomes`):
            cost_nu, cost_usd, cost_complete (0/1), price_period
        Return the USD, so the run's budget guard can use it.

Step 3. Total cost of a run is just a SQL sum. `run_usd` below already does it,
        and `summary()` already sums cost_nu / cost_usd per run and per agent.
        Nothing else to change: scripts/report.py prints them once they are filled.

Step 4. Test it. tests/unit/test_ledger.py already has the acceptance tests
        (they are skipped until `cost` exists). One uses a real bill from the v0
        pilot: 151,528 uncached + 318,700 cached + 17,434 output = $0.202926.
            .venv/bin/python -m pytest tests/unit/test_ledger.py -v
        Then re-price runs already in the DB by re-running the report:
            .venv/bin/python scripts/report.py --label check5
        (For runs recorded before you built this, write a small backfill that
        reads each outcomes row, calls cost(), and UPDATEs the four columns.)

IMPORTANT until this is built: `run_usd` returns 0, so the per-instance and
per-label dollar budgets in config/econocontext.yaml cannot stop a paid run.
Only the step limit (limits.per_instance_step_limit model calls) caps it.

Not in scope: the Jev decider's own cost. Only the agent's model calls count.
------------------------------------------------------------------------------
"""

import json
from datetime import date, datetime, timezone

from ..config import PriceCard
from ..store.db import AgentDB
from ..types import ProviderUsage


def cost(usage: ProviderUsage, card: PriceCard, day: date) -> tuple[float, float, bool, str]:
    """Return (cost_nu, cost_usd, complete, price_period) for one call. See Step 1."""
    # PLACEHOLDER: not built yet (see Step 1 in the module docstring).
    raise NotImplementedError("ledger.cost is not built yet: see econocontext/pricing/ledger.py")


class Ledger:
    def __init__(self, db: AgentDB, card: PriceCard):
        self.db, self.card = db, card

    def record(self, outcome_id: str, run_id: str, agent_id: str, decision_id: str | None,
               phase: str, usage: ProviderUsage, on: date | None = None) -> float:
        """Save one call's usage. Returns its USD cost (0.0 until cost() is built)."""
        day = on or datetime.now(timezone.utc).date()  # the date picks the price period
        # PLACEHOLDER: Step 2. Replace the Nones with cost(usage, self.card, day).
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
