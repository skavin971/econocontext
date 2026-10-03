"""Edit quote and status line, with hand-computed answers.

`count` here is one token per character of content, so every number is checkable
by eye. Prices: $0.75 / $0.075 per 1M (in / cached).
"""

import pytest

from econoclm.quote.edit_quote import edit_quote
from econoclm.quote.messages import first_change, fmt_tokens
from econoclm.quote.status_line import status_line

IN, CACHED = 0.75e-6, 0.075e-6


def count(msgs):
    return sum(len(m.get("content") or "") for m in msgs)


def msg(role, n, tag=""):
    return {"role": role, "content": (tag + "x" * n)[:max(n, len(tag))]}


# system 100 + task 100, then four editable turns of 100 each: 600 tokens.
BEFORE = [msg("system", 100), msg("user", 100), msg("assistant", 100),
          msg("user", 100, "[obs 1]"), msg("assistant", 100), msg("user", 100, "[obs 2]")]


def test_edit_only_last_message_rereads_nothing():
    after = BEFORE[:5] + [msg("user", 20)]
    # The last call cached its prefix but not the newest message (Gemini's lag).
    q = edit_quote(BEFORE, after, cached_c=480, count=count)
    assert q.first_change_msg == 5
    assert q.prefix_tokens_p == 500
    assert q.R == 0
    assert q.extra_usd == 0
    assert q.removed == [2]


def test_edit_at_first_editable_message():
    after = BEFORE[:2] + [msg("assistant", 10)] + BEFORE[3:]
    q = edit_quote(BEFORE, after, cached_c=480, count=count)
    assert q.first_change_msg == 2 and q.turn == 1
    assert q.prefix_tokens_p == 200            # system + task
    assert q.R == 480 - 200                    # c - (system + task)
    assert q.extra_usd == pytest.approx(280 * (IN - CACHED))
    assert (q.before_tokens, q.after_tokens) == (600, 510)
    assert q.saving_usd == pytest.approx(90 * CACHED)
    assert q.payoff_calls == pytest.approx(280 * (IN - CACHED) / (90 * CACHED))  # = 28
    assert "first change at turn 1: ~280 re-read next call" in q.line
    assert "pays off after ~28 calls" in q.line
    assert q.line.startswith("[econo] edit: 600→510 tokens (−90)")


def test_cache_unknown():
    after = BEFORE[:3] + [msg("user", 10)] + BEFORE[4:]
    q = edit_quote(BEFORE, after, cached_c=None, count=count)
    assert q.R is None and q.payoff_calls is None
    assert "cache unknown" in q.line
    assert "removed: obs 1 (stored: econo get 1)" in q.line


def test_growth_has_no_payoff():
    after = BEFORE[:5] + [msg("user", 300, "[obs 2]")]
    q = edit_quote(BEFORE, after, cached_c=400, count=count)
    assert q.saving_usd == 0 and q.payoff_calls is None
    assert "pays off after n/a" in q.line and "(+200)" in q.line


def test_no_change_gives_no_quote():
    assert edit_quote(BEFORE, [dict(m) for m in BEFORE], 100, count=count) is None


def test_calibration_scales_positions():
    after = BEFORE[:2] + [msg("assistant", 10)] + BEFORE[3:]
    q = edit_quote(BEFORE, after, cached_c=480, count=count, k=1.2)
    assert q.prefix_tokens_p == 240 and q.R == 240


def test_first_change_sees_tool_structure():
    a = [{"role": "assistant", "content": "t",
          "tool_calls": [{"function": {"name": "bash", "arguments": "{\"command\":\"ls\"}"}}]}]
    flat = [{"role": "assistant", "content": "t"}]
    assert first_change(a, flat) == 0
    assert first_change(a, [dict(a[0])]) is None


def test_status_line_depths_and_format():
    # Editable region = tokens 200..600. Depths at 300, 400, 500 -> turns 2, 3, 4.
    st = status_line(BEFORE, cached_c=480, uncached=120, run_cost_usd=0.021, n_stored=3,
                     stale=["/app/src/parser.py"], count=count)
    assert [d.turn for d in st.depths] == [2, 3, 4]
    assert [d.reread for d in st.depths] == [180, 80, 0]
    assert st.depths[0].cost_usd == pytest.approx(180 * (IN - CACHED))
    assert st.line.startswith("[econo] last call 480 cached / 120 new | run $0.021 | edit at turn ≤2: ~180 re-read")
    assert st.line.endswith("| stored: 3 | stale: parser.py")


def test_status_line_respects_clm_flattening():
    # A structured (tool) turn at message 3: any edit rewrites from there, so the
    # deeper depths cost at least as much as an edit at message 3.
    msgs = [dict(m) for m in BEFORE]
    msgs[3] = {"role": "tool", "content": "y" * 100, "tool_call_id": "c1"}
    st = status_line(msgs, cached_c=480, uncached=0, run_cost_usd=0, n_stored=0, stale=[],
                     count=count)
    assert [d.reread for d in st.depths] == [180, 180, 180]


def test_status_line_cache_unknown_and_stale_overflow():
    st = status_line(BEFORE, cached_c=None, uncached=None, run_cost_usd=0.0, n_stored=0,
                     stale=["a", "b", "c", "d", "e"], count=count)
    assert "last call cache unknown" in st.line
    assert "stale: a, b, c +2 more" in st.line


def test_fmt_tokens():
    assert [fmt_tokens(x) for x in (950, 14234, 152000)] == ["950", "14.2K", "152K"]
