"""Acceptance tests for econocontext/pricing/ledger.py.

    .venv/bin/python -m pytest tests/unit/test_ledger.py -v
"""

from datetime import date

import pytest

from econocontext.config import PriceCard, PriceTier
from econocontext.pricing import ledger
from econocontext.store.db import AgentDB
from econocontext.types import ProviderUsage


def test_reproduces_a_real_pilot_bill_to_the_cent(cfg):
    # v0/docs/runs/2026-09-25-live-study/pilot-gemini/ledger.jsonl, first row:
    # PyCQA__pyflakes-325 P0 on Gemini 3.6 Flash, billed $0.202926 at promo rates.
    usage = ProviderUsage(uncached_input=151_528, cache_read=318_700, cache_write=None,
                          output=17_434, cache_write_applicable=False)
    nu, usd, complete, period = ledger.cost(usage, cfg.card, date(2026, 9, 25))
    assert usd == pytest.approx(0.202926, abs=1e-6) and complete
    assert nu == pytest.approx(151_528 + 318_700 * 0.1 + 17_434 * 5.0)
    assert "2026-12-31" in period


def test_the_same_usage_costs_double_after_the_promotion(cfg):
    usage = ProviderUsage(151_528, 318_700, None, 17_434, cache_write_applicable=False)
    _, promo, _, _ = ledger.cost(usage, cfg.card, date(2026, 12, 31))
    _, standard, _, _ = ledger.cost(usage, cfg.card, date(2027, 1, 1))
    assert standard == pytest.approx(2 * promo)


def test_a_missing_counter_is_incomplete_not_zero(cfg):
    _, _, complete, _ = ledger.cost(
        ProviderUsage(100, None, None, 10, cache_write_applicable=False),
        cfg.card, date(2026, 9, 27))
    assert not complete


def test_prompt_tier_is_selected_by_reported_prompt_size():
    card = PriceCard(
        provider="test", model="test", source_url="test", retrieved_on="2026-01-01",
        min_cacheable_tokens=None,
        periods=[{"valid_from": None, "valid_until": None, "tiers": [
            PriceTier(100, 1.0, 2.0, 0.5, 1.0),
            PriceTier(None, 2.0, 4.0, 1.0, 2.0),
        ]}],
    )
    small = ProviderUsage(100, 0, None, 10, cache_write_applicable=False)
    large = ProviderUsage(101, 0, None, 10, cache_write_applicable=False)
    assert ledger.cost(small, card, date(2026, 9, 27))[1] == pytest.approx(0.00012)
    assert ledger.cost(large, card, date(2026, 9, 27))[1] == pytest.approx(0.000242)


def test_implicit_cache_write_is_not_charged_when_not_applicable(cfg):
    base = ProviderUsage(100, 900, None, 10, cache_write_applicable=False)
    noisy = ProviderUsage(100, 900, 999_999, 10, cache_write_applicable=False)
    assert ledger.cost(base, cfg.card, date(2026, 9, 27)) == \
        ledger.cost(noisy, cfg.card, date(2026, 9, 27))


def test_ledger_persists_cost_and_reports_incomplete_runs(cfg, tmp_path):
    db = AgentDB(tmp_path / "db.sqlite3")
    db.start_run("r", "test", None, "econo", "observe", cfg.card.model, 1.0, "fp")
    book = ledger.Ledger(db, cfg.card)
    usage = ProviderUsage(100, 900, None, 10, cache_write_applicable=False)
    assert book.record("o1", "r", "r:root", None, "agent", usage,
                       date(2026, 9, 27)) == pytest.approx(0.00018)
    row = db.rows("SELECT cost_nu, cost_usd, cost_complete, price_period FROM outcomes")[0]
    assert row["cost_nu"] == pytest.approx(240) and row["cost_complete"] == 1
    assert "2026-12-31" in row["price_period"]
    assert book.run_cost("r")["complete"]

    book.record("o2", "r", "r:root", None, "agent",
                ProviderUsage(100, None, None, 10, cache_write_applicable=False),
                date(2026, 9, 27))
    state = book.run_cost("r")
    assert state["incomplete_calls"] == 1 and not state["complete"]
