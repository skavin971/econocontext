"""Acceptance tests for econocontext/pricing/ledger.py. Skipped until cost() is built.

    .venv/bin/python -m pytest tests/unit/test_ledger.py -v
"""

from datetime import date

import pytest

from econocontext.pricing import ledger
from econocontext.types import ProviderUsage


def cost(*args):
    """The acceptance tests below are skipped until ledger.cost is built."""
    try:
        return ledger.cost(*args)
    except NotImplementedError:
        pytest.skip("ledger.cost is not built yet (see econocontext/pricing/ledger.py)")


def test_reproduces_a_real_pilot_bill_to_the_cent(cfg):
    # v0/docs/runs/2026-09-25-live-study/pilot-gemini/ledger.jsonl, first row:
    # PyCQA__pyflakes-325 P0 on Gemini 3.6 Flash, billed $0.202926 at promo rates.
    usage = ProviderUsage(uncached_input=151_528, cache_read=318_700, cache_write=None, output=17_434)
    nu, usd, complete, period = cost(usage, cfg.card, date(2026, 9, 25))
    assert usd == pytest.approx(0.202926, abs=1e-6) and complete
    assert nu == pytest.approx(151_528 + 318_700 * 0.1 + 17_434 * 5.0)
    assert "2026-12-31" in period


def test_the_same_usage_costs_double_after_the_promotion(cfg):
    usage = ProviderUsage(151_528, 318_700, None, 17_434)
    _, promo, _, _ = cost(usage, cfg.card, date(2026, 12, 31))
    _, standard, _, _ = cost(usage, cfg.card, date(2027, 1, 1))
    assert standard == pytest.approx(2 * promo)


def test_a_missing_counter_is_incomplete_not_zero(cfg):
    _, _, complete, _ = cost(ProviderUsage(100, None, None, 10), cfg.card, date(2026, 9, 27))
    assert not complete
