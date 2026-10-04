"""Per-turn hidden-thinking accounting (quote/hidden.py) and where it is used.

`count` is one token per character of content (as in test_quote.py), and every
signed assistant turn's thinking is set by hand, so every number is checkable by eye.
"""

import asyncio

import pytest

from econoclm.analysis.rereads import call_rereads
from econoclm.core.gateway_ledger import Ledger
from econoclm.quote.edit_quote import edit_quote
from econoclm.quote.hidden import (hidden_per_message, live_start, provider_positions,
                                   sig_key, signature)
from econoclm.quote.status_line import status_line

IN, CACHED = 0.75e-6, 0.075e-6


def count(msgs):
    return sum(len(m.get("content") or "") for m in msgs)


def call(sig, n=10):
    """A signed tool-call assistant turn whose visible text is n tokens."""
    return {"role": "assistant", "content": "x" * n, "tool_calls": [{
        "id": sig, "type": "function", "function": {"name": "bash", "arguments": "{}"},
        "extra_content": {"google": {"thought_signature": sig}}}]}


def tool(n=10):
    return {"role": "tool", "content": "y" * n, "tool_call_id": "t"}


def user(n=10):
    return {"role": "user", "content": "u" * n}


THINK = {sig_key("S1"): 1000, sig_key("S2"): 2000, sig_key("S3"): 4000}
TASK = [{"role": "system", "content": "s" * 100}, user(100)]


def test_signature_and_unsigned_turns():
    assert signature(call("S1")) == "S1"
    assert signature({"role": "assistant", "content": "plain text"}) is None
    assert signature(tool()) is None


def test_thinking_accumulates_within_a_run_of_tool_calls():
    msgs = TASK + [call("S1"), tool(), call("S2"), tool()]
    assert live_start(msgs) == 1                     # the task message, answered
    assert hidden_per_message(msgs, THINK) == [0, 0, 1000, 0, 2000, 0]


def test_answered_user_message_resets_the_run():
    # A CLM nudge (user) after S2, then the reply S3: S1 and S2 stop counting, S3 counts.
    msgs = TASK + [call("S1"), tool(), call("S2"), tool(), user(), call("S3"), tool()]
    assert hidden_per_message(msgs, THINK) == [0, 0, 0, 0, 0, 0, 0, 4000, 0]


def test_unanswered_user_message_does_not_reset_yet():
    msgs = TASK + [call("S1"), tool(), call("S2"), tool(), user()]
    assert sum(hidden_per_message(msgs, THINK)) == 3000


def test_rebuilt_turns_carry_nothing():
    # After CLM's rebuild every editable turn is plain text: no signatures survive.
    msgs = TASK + [{"role": "assistant", "content": "x" * 10}, user()]
    assert sum(hidden_per_message(msgs, THINK)) == 0


def test_unknown_signature_is_estimated_from_its_length():
    sig = "A" * 510                                  # 510 chars / 5.1 per token
    assert hidden_per_message(TASK + [call(sig), tool()], {}) == [0, 0, 100, 0]


def test_no_thinking_map_means_no_hidden():
    assert hidden_per_message(TASK + [call("S1")], None) == [0, 0, 0]


def test_provider_positions_add_hidden_to_scaled_visible():
    msgs = TASK + [call("S1"), tool(), call("S2"), tool()]
    pos = provider_positions(msgs, count, k=2.0, thinking=THINK)
    # visible: 100, 100, 10, 10, 10, 10 (x2) ; hidden 1000 at S1, 2000 at S2
    assert pos == [0, 200, 400, 1420, 1440, 3460, 3480]


def test_edit_quote_counts_hidden_in_position_and_saving():
    before = TASK + [call("S1"), tool(), call("S2"), tool()]
    after = TASK + [{"role": "assistant", "content": "x" * 10}, user(10)]  # CLM's rebuild
    q = edit_quote(before, after, cached_c=3000, count=count, thinking=THINK)
    assert q.first_change_msg == 2
    assert q.prefix_tokens_p == 200                  # system + task: no hidden before
    assert (q.before_tokens, q.after_tokens) == (3240, 220)
    assert (q.hidden_before, q.hidden_after) == (3000, 0)
    assert q.R == 2800
    assert q.saving_usd == pytest.approx(3020 * CACHED)  # the thinking is gone too
    assert "earlier thinking 3.0K→0" in q.line


def test_edit_quote_without_thinking_is_unchanged():
    before = TASK + [call("S1"), tool()]
    after = TASK + [{"role": "assistant", "content": "x" * 10}]
    q = edit_quote(before, after, cached_c=200, count=count)
    assert (q.before_tokens, q.after_tokens, q.hidden_before) == (220, 210, 0)
    assert "earlier thinking" not in q.line


def test_status_line_shows_what_gemini_reads():
    msgs = TASK + [call("S1"), tool(), call("S2"), tool()]
    st = status_line(msgs, cached_c=3000, uncached=240, run_cost_usd=0.01, n_stored=2,
                     stale=[], count=count, thinking=THINK, read=(3240, 240, 3000))
    assert "Gemini read 3.2K, CLM counts 240, earlier thinking 3.0K" in st.line
    # Any edit rewrites from the first structured turn (index 2, CLM's flattening), so
    # every depth re-reads everything cached after system + task: 3000 - 200.
    assert [d.reread for d in st.depths] == [2800, 2800, 2800]


def test_rereads_calibrates_k_on_visible_and_skips_retry_rows():
    s0 = TASK
    s1 = TASK + [call("S1"), tool()]
    s2 = TASK + [call("S1"), tool(), call("S2"), tool()]
    row = lambda p, c, r, ts, fin="tool_calls": {  # noqa: E731
        "prompt_tokens": p, "cached_tokens": c, "uncached_tokens": p - c,
        "reasoning_tokens": r, "finish_reason": fin, "ts": ts}
    calls = [row(200, 0, 1000, 0.0),
             row(9999, 0, 0, 1.0, "malformed_function_call"),   # resent below CLM's count
             row(1220, 200, 2000, 2.0),
             row(3240, 1220, 0, 5.0)]
    rr = call_rereads([s0, s1, s2], calls, count=count)
    assert [r["hidden"] for r in rr] == [1000, 3000]
    assert [r["k_visible"] for r in rr] == [1.0, 1.0]
    assert [r["appended"] for r in rr] == [1020, 2020]   # new visible + the new thinking
    assert [r["extra_uncached"] for r in rr] == [0, 0]
    assert [r["gap_s"] for r in rr] == [2.0, 3.0]


def test_hooks_record_thinking_and_calibrate_on_visible(tmp_path):
    from econoclm.tests.test_agent_hooks import hooks

    class Usage:
        def __init__(self, p, r):
            self.d = {"prompt_tokens": p, "completion_tokens": 5, "total_tokens": p + 5 + r,
                      "prompt_tokens_details": {"cached_tokens": 0},
                      "completion_tokens_details": {"reasoning_tokens": r}}

        def model_dump(self):
            return self.d

    class Msg:
        def __init__(self, sig):
            self.sig = sig

        def model_dump(self):
            return call(self.sig)

    class Choice:
        def __init__(self, sig):
            self.message = Msg(sig)

    class Response:
        def __init__(self, p, r, sig):
            self.usage, self.choices = Usage(p, r), [Choice(sig)]

    h = hooks(tmp_path)
    h.count = count
    first = TASK
    asyncio.run(h.on_response(Response(400, 1000, "S1"), first))
    assert h.thinking == {sig_key("S1"): 1000}
    assert h.k == pytest.approx(400 / 200) and h.last_read == (400, 200, 0)
    second = TASK + [call("S1"), tool()]
    asyncio.run(h.on_response(Response(1440, 0, "S2"), second))
    # Gemini read 1440 = 1000 hidden + 220 visible scaled by k = 440 / 220 = 2.
    assert h.last_read == (1440, 220, 1000)
    assert h.k == pytest.approx(2.0)


def test_ledger_fallback_skips_retry_rows(tmp_path):
    from econoclm.tests.test_agent_hooks import hooks
    db = tmp_path / "gateway.sqlite"
    ledger = Ledger(db)
    for fin, cached in (("tool_calls", 10), ("malformed_function_call", 99), ("tool_calls", 20)):
        ledger.insert("econo-t-r1", prompt_tokens=100, cached_tokens=cached, uncached_tokens=0,
                      finish_reason=fin, http_status=200)
    h = hooks(tmp_path, gateway_db=db)
    assert asyncio.run(h.ledger_row(1))["cached_tokens"] == 20


def test_meter_snaps_to_the_measured_state_and_recalibrates_k():
    from econoclm.quote.hidden import HiddenMeter
    m = HiddenMeter()
    assert m.observe(TASK, 400, 200, THINK) == 0 and m.k == 2.0          # calibration call
    nudged = TASK + [call("S1"), tool(), call("S2"), tool(), user(), call("S3"), tool()]
    ours = count(nudged)                                                   # 270 -> visible 540
    assert m.observe(nudged, 540 + 4000, ours, THINK) == 4000 and m.mode == "live"
    assert m.observe(nudged, 540 + 7000, ours, THINK) == 7000 and m.mode == "all"
    assert m.k == 2.0                    # clear-cut states keep k at (prompt - hidden) / ours
    assert m.observe(nudged, 540 + 30, ours, THINK) == 0 and m.mode == "none"   # within noise
    assert m.k == pytest.approx(570 / 270)   # clear-cut "none": the 30 tokens were visible


def test_meter_waits_for_calibration():
    from econoclm.quote.hidden import HiddenMeter
    m = HiddenMeter()
    assert m.observe(TASK + [call("S1"), tool()], 3000, 220, THINK) == 0 and not m.calibrated
