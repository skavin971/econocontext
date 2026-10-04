"""Usage JSON -> billed tokens and anomaly flags. No API calls."""

import pytest

from econoclm.core import prices
from econoclm.core.gateway_ledger import Ledger
from econoclm.core.meter import price_call_usd
from econoclm.core.usage import billed_output, to_usage

# Gate 2 on 2026-10-03: max_tokens 16, thinking used it up, no visible text, and Vertex
# left completion_tokens out. The printout showed these fields and values; total_tokens
# was present but not printed, so 6 + 13 is assumed.
GATE2_THINKING_ONLY = {"prompt_tokens": 6, "total_tokens": 19, "extra_properties": {},
                       "completion_tokens_details": {"reasoning_tokens": 13}}


def test_thinking_only_reply_bills_the_thinking():
    assert billed_output(GATE2_THINKING_ONLY) == (13, False)
    u = to_usage(GATE2_THINKING_ONLY)
    assert (u.uncached_input, u.cache_read, u.output, u.reasoning) == (6, 0, 13, 13)
    assert price_call_usd(u, prices.RATES) == pytest.approx(
        6 * prices.PRICE_IN + 13 * prices.PRICE_OUT)


def test_thinking_only_reply_without_total():
    usage = {k: v for k, v in GATE2_THINKING_ONLY.items() if k != "total_tokens"}
    assert billed_output(usage) == (13, False)


def test_normal_reply():
    usage = {"prompt_tokens": 500, "completion_tokens": 10, "total_tokens": 530,
             "completion_tokens_details": {"reasoning_tokens": 20}}
    assert billed_output(usage) == (30, False)
    u = to_usage(usage)
    assert (u.uncached_input, u.cache_read, u.output, u.reasoning) == (500, 0, 30, 20)


def test_reply_without_reasoning_details():
    usage = {"prompt_tokens": 500, "completion_tokens": 10, "total_tokens": 510}
    assert billed_output(usage) == (10, False)


def test_reply_with_cached_tokens():
    usage = {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1150,
             "prompt_tokens_details": {"cached_tokens": 600},
             "completion_tokens_details": {"reasoning_tokens": 50}}
    u = to_usage(usage)
    assert (u.uncached_input, u.cache_read, u.output, u.reasoning) == (400, 600, 150, 50)
    assert price_call_usd(u, prices.RATES) == pytest.approx(
        400 * prices.PRICE_IN + 600 * prices.PRICE_CACHED + 150 * prices.PRICE_OUT)
    assert billed_output(usage)[1] is False


def test_totals_mismatch_is_an_anomaly():
    # The OpenAI convention (reasoning inside completion_tokens): the total is 50 short.
    usage = {"prompt_tokens": 1000, "completion_tokens": 150, "total_tokens": 1150,
             "completion_tokens_details": {"reasoning_tokens": 50}}
    assert billed_output(usage) == (200, True)


def test_total_smaller_than_the_reported_part_is_an_anomaly():
    usage = {"prompt_tokens": 100, "total_tokens": 105,
             "completion_tokens_details": {"reasoning_tokens": 13}}
    assert billed_output(usage) == (5, True)


def test_no_output_count_at_all():
    assert billed_output({"prompt_tokens": 6}) == (None, False)  # the gateway flags it
    assert billed_output(None) == (None, False)


def test_old_ledger_gets_the_new_columns(tmp_path):
    import sqlite3
    path = tmp_path / "gateway.sqlite"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE calls (run_id TEXT NOT NULL, call_no INTEGER NOT NULL, "
                "ts REAL NOT NULL, cost_usd REAL, PRIMARY KEY (run_id, call_no))")
    old.execute("INSERT INTO calls VALUES ('smoke', 0, 0, 0.0000045)")
    old.commit()
    old.close()
    ledger = Ledger(path)
    ledger.insert("smoke", cost_usd=0.001, usage_anomaly=0, raw_usage='{"prompt_tokens": 6}')
    got = ledger.calls("smoke")
    assert [r["usage_anomaly"] for r in got] == [None, 0]
    assert [r["raw_usage"] for r in got] == [None, '{"prompt_tokens": 6}']
    ledger.close()
