"""EconoContext's decision prices (econocontext/pricing/lifecycle.py): the price card, the arrival
price, keep versus preview, and the compaction break-even.

Moved verbatim from tests/v2/test_rules.py when the Claude Code hook tests were archived (step 2).
"""

import pytest

from econocontext.pricing import lifecycle

PRICES = lifecycle.from_card("anthropic", "claude-sonnet-5")


# Pricing ---------------------------------------------------------------------------------
def test_prices_come_from_the_card():
    assert (PRICES.cache_read, PRICES.cache_write, PRICES.output) == (0.1, 1.25, 5.0)
    assert PRICES.c == pytest.approx(0.95 * 0.1 + 0.05)          # 0.145 per resident token per call


def test_worked_arrival_example():
    # 5,000 tokens with 20 calls left: full = 5000·1.25 + 5000·0.145·20 = 20,750
    prices = lifecycle.arrival(PRICES, 5000, {"preview": 100}, {"preview": 0.1}, 20, 40_000, reread_at=10)
    assert prices["full"] == pytest.approx(20_750)
    reread = 40_000 * 0.145 + 500 * 5 + 5000 * 1.25 + 5000 * 0.145 * 10   # re-read at call 10, carried 10
    assert prices["preview"] == pytest.approx(100 * 1.25 + 100 * 0.145 * 20 + 0.1 * reread)


def test_content_needed_soon_is_cheaper_to_keep():
    # 5,000 tokens, 25 calls left, 95% needed again within a call: keeping it beats a preview.
    soon = lifecycle.arrival(PRICES, 5000, {"preview": 150}, {"preview": 0.95}, 25, 40_000,
                             lifecycle.reread_at("few_calls", 25))
    assert soon["full"] < soon["preview"]
    late = lifecycle.arrival(PRICES, 5000, {"preview": 150}, {"preview": 0.05}, 25, 40_000,
                             lifecycle.reread_at("whole_task", 25))
    assert late["preview"] < late["full"]


def test_compaction_break_even():
    prices = lifecycle.compaction(PRICES, 100_000, 36_400, 1_500, 20, [])
    assert prices["keep"] == pytest.approx(63_600 * 0.145 * 20)
    fixed = 100_000 * 0.145 + 1_500 * 5 + 1_500 * 1.25
    assert prices["h_star"] == pytest.approx(fixed / ((63_600 - 1_500) * 0.145))
    assert prices["compact"] < prices["keep"]


def test_measured_compaction_cost_makes_compaction_rare():
    # v2x2 maven at call 13: prompt ~60k, 25 calls total. With the measured summary (10,630 kept,
    # 16,783 output) compaction does not pay; with the old 1,500 placeholder it looked like it did.
    measured = lifecycle.compaction(PRICES, 60_000, 36_400, 10_630, 12, [], summary_output=16_783)
    assert measured["compact"] > measured["keep"] and measured["h_star"] > 40
    placeholder = lifecycle.compaction(PRICES, 60_000, 36_400, 1_500, 12, [])
    assert placeholder["h_star"] < measured["h_star"]
