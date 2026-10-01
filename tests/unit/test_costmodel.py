"""The meter and the placement options, against a call-by-call simulation.

The closed forms in costmodel/meter.py must equal pricing each call of the loop with
price_call; the options must reproduce what wctl4/wres4 showed (docs/omnigent-findings.md).
"""

import pytest

from econocontext.costmodel.meter import (ExplicitCache, ImplicitCache, Loop, fit_implicit,
                                          price_call, price_quantiles)
from econocontext.costmodel.options import (Shape, break_even_calls_saved,
                                            commit_pending_break_even, fresh, handoff, resume)
from econocontext.types import ProviderUsage, RateRatios

# Claude Sonnet 5 ($2 in, $10 out, 0.20 read, 2.50 write) and Gemini 3.6 Flash, as ratios.
CLAUDE = RateRatios("claude-sonnet-5", 5.0, 0.1, 1.25, 2.0, 2e-6)
GEMINI = RateRatios("gemini-3.6-flash", 5.0, 0.1, 1.0, None, 0.75e-6)
# Claude Code's Explore worker, measured by harness/explain_placement.py.
EXPLORE = Shape(shared_prefix=8011, first_tail=3520, growth=2073, output=567, resume_tail=2900)


def by_call_explicit(loop: Loop, r: RateRatios) -> float:
    total, prompt = 0.0, loop.cached + loop.first
    for k in range(int(loop.calls)):
        read, write = (loop.cached, loop.first) if k == 0 else (prompt, loop.growth)
        if k:
            prompt += loop.growth
        total += price_call(ProviderUsage(0, int(read), int(write), int(loop.output)), r)
    return total


def by_call_implicit(loop: Loop, cache: ImplicitCache, r: RateRatios) -> float:
    total, previous, prompt = 0.0, loop.cached, loop.cached + loop.first
    for k in range(int(loop.calls)):
        read = cache.read(previous) if (k or loop.cached) else 0.0
        total += (prompt - read) * r.input_ratio + read * r.cache_read_ratio + loop.output * r.output_ratio
        previous, prompt = prompt, prompt + loop.growth
    return total


def test_price_call_counts_every_counter_and_the_1h_writes():
    u = ProviderUsage(uncached_input=10, cache_read=1000, cache_write=100, output=20, cache_write_1h=50)
    assert price_call(u, CLAUDE) == pytest.approx(10 + 100 + 125 + 100 + 100)
    gemini = ProviderUsage(10, 1000, None, 20, cache_write_applicable=False)
    assert price_call(gemini, GEMINI) == pytest.approx(10 + 100 + 100)


@pytest.mark.parametrize("loop", [Loop(8011, 3902, 5, 4173, 624), Loop(0, 30000, 3, 1500, 300),
                                  Loop(30256, 3273, 9, 1814, 490), Loop(5000, 100, 1, 999, 10)])
def test_closed_forms_equal_pricing_each_call(loop):
    assert ExplicitCache().price(loop, CLAUDE) == pytest.approx(by_call_explicit(loop, CLAUDE))
    cache = ImplicitCache(miss=0.19, lag=2539)
    assert cache.price(loop, GEMINI) == pytest.approx(by_call_implicit(loop, cache, GEMINI))


def test_the_implicit_cache_is_fitted_from_hits_and_misses():
    cache = fit_implicit([(12200, 9628), (12517, 0), (12636, 11575), (15009, 13557)])
    assert cache.miss == pytest.approx(0.25) and cache.lag == 1452  # gaps 2572, 1061, 1452


def test_quantiles_grow_with_the_call_count():
    q10, q50, q90 = price_quantiles(Loop(8011, 3902, 5, 4173, 624), ExplicitCache(), CLAUDE, (3, 5, 9))
    assert q10 < q50 < q90


def test_the_follow_ups_of_wctl4_and_wres4_are_reproduced_from_their_call_counts():
    # billed: FRESH 49,845 NU in 5 calls; RESUME 82,314 NU in 9 (a warm 30,256-token history)
    assert ExplicitCache().price(Loop(8011, 3902, 5, 4173, 624), CLAUDE) == pytest.approx(49845, rel=0.05)
    assert ExplicitCache().price(Loop(30256, 3273, 9, 1814, 490), CLAUDE) == pytest.approx(82314, rel=0.05)


def test_at_the_same_call_count_a_new_worker_is_cheaper_than_a_resumed_one():
    task = 400
    for calls in (3, 5, 10, 20):
        new = ExplicitCache().price(fresh(EXPLORE, task, calls), CLAUDE)
        warm = ExplicitCache().price(resume(EXPLORE, 30256, True, task, calls), CLAUDE)
        assert new < warm, calls  # 30K of history is read on every call, 8K of prefix is not more
    assert resume(EXPLORE, 30256, False, task, 5) is None  # a cold worker is never priced


def test_a_cold_history_costs_more_on_its_first_call_than_a_new_workers_whole_run():
    new_run = ExplicitCache().price(fresh(EXPLORE, 400, 5), CLAUDE)
    cold_first_call = ExplicitCache().price(Loop(0, 30000 + 400, 1, 0, 567), CLAUDE)
    assert cold_first_call > 0.75 * new_run and 30000 * 1.25 > 0.9 * new_run


def test_resume_must_save_calls_to_win_and_handoff_pays_only_for_its_brief():
    f, r = fresh(EXPLORE, 400, 10), resume(EXPLORE, 30256, True, 400, 10)
    saved = break_even_calls_saved(f, r, ExplicitCache(), CLAUDE)
    assert 1.5 < saved < 3  # ~2 fewer calls of 10, as the wres4 replay found
    shorter = Loop(r.cached, r.first, 10 - saved, r.growth, r.output)
    assert ExplicitCache().price(shorter, CLAUDE) == pytest.approx(ExplicitCache().price(f, CLAUDE), rel=1e-6)
    brief = handoff(EXPLORE, 400, 3000, 10)
    extra = ExplicitCache().price(brief, CLAUDE) - ExplicitCache().price(f, CLAUDE)
    assert extra == pytest.approx(3000 * 1.25 + 9 * 3000 * 0.1)  # written once, read by each later call


def test_commit_pending_break_even_equals_the_simulated_crossing():
    after, saved, prompt, growth = 1227, 878, 23931, 900
    n_star = commit_pending_break_even(after, saved, CLAUDE)
    assert n_star == pytest.approx(4.57, abs=0.01)  # c4

    def cost(commit: bool, n: float) -> float:
        cached, first = (prompt - after, after - saved + growth) if commit else (prompt, growth)
        return ExplicitCache().price(Loop(cached, first, n, growth, 345), CLAUDE)
    assert cost(True, n_star) == pytest.approx(cost(False, n_star), rel=1e-9)
    assert cost(True, n_star + 1) < cost(False, n_star + 1) and cost(True, n_star - 1) > cost(False, n_star - 1)
