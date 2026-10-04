"""The analysis scripts on a small synthetic day (no real run needed)."""

import json

import pytest

from econoclm.analysis import edit_ceiling, quote_check, results_table
from econoclm.analysis.common import load_runs
from econoclm.analysis.rereads import call_rereads
from econoclm.core import prices
from econoclm.core.gateway_ledger import Ledger
from econoclm.core.run_store import RunStore


def count(msgs):
    return sum(len(m.get("content") or "") for m in msgs)


def tool_turn(n):
    return [{"role": "assistant", "content": "a" * 10,
             "tool_calls": [{"function": {"name": "bash", "arguments": "{}"}}]},
            {"role": "tool", "content": "o" * n, "tool_call_id": "c"}]


BASE = [{"role": "system", "content": "s" * 100}, {"role": "user", "content": "t" * 100}]
S0 = BASE
S1 = BASE + tool_turn(90)                                   # append-only
S2 = BASE + [{"role": "user", "content": "summary"}] + tool_turn(40)  # rewrite at msg 2


def test_call_rereads_hand_case():
    calls = [{"prompt_tokens": count(s), "uncached_tokens": u, "cached_tokens": 0}
             for s, u in ((S0, 200), (S1, 120), (S2, 57))]
    rr = call_rereads([S0, S1, S2], calls, count=count)
    assert rr[0]["rewrite"] is False and rr[0]["appended"] == 100 and rr[0]["extra_uncached"] == 20
    assert rr[1]["rewrite"] is True and rr[1]["first_change"] == 2
    assert rr[1]["appended"] == 50 and rr[1]["extra_uncached"] == 7
    assert rr[1]["extra_usd"] == pytest.approx(7 * (prices.PRICE_IN - prices.PRICE_CACHED))


def make_day(tmp_path):
    day = tmp_path / "2026-10-03"
    ledger = Ledger(day / "gateway.sqlite")
    for arm in ("raw", "econo"):
        run_id = f"{arm}-task-a-r1"
        (day / run_id).mkdir(parents=True)
        (day / run_id / "command.txt").write_text("harbor trial start ...\n")
        trial = day / "harbor" / run_id
        snaps = trial / "agent" / "context_snapshots"
        snaps.mkdir(parents=True)
        (trial / "result.json").write_text(json.dumps({
            "verifier_result": {"rewards": {"reward": 1.0 if arm == "econo" else 0.0}},
            "agent_execution": {"started_at": "2026-10-03T10:00:00+00:00",
                                "finished_at": "2026-10-03T10:05:00+00:00"}}))
        (trial / "agent" / "usage.json").write_text(json.dumps(
            {"n_lm_calls": 3, "n_ctx_syncs": 1, "n_retry_on_limit": 0}))
        (trial / "agent" / "timing.json").write_text(json.dumps(
            [{"cmd": "ls", "bash_s": 0.1}, {"cmd": "ls", "bash_s": 0.1}]))
        for i, s in enumerate((S0, S1, S2)):
            (snaps / f"turn-{i:04d}.json").write_text(json.dumps(
                {"step": i, "tokens": count(s), "kind": "agent", "messages": s}))
        for s, u in ((S0, 200), (S1, 120), (S2, 57)):
            ledger.insert(run_id, prompt_tokens=count(s), cached_tokens=count(s) - u,
                          uncached_tokens=u, output_tokens=10, reasoning_tokens=0,
                          cost_usd=0.01, latency_ms=500, finish_reason="stop", http_status=200,
                          upstream_attempts=1, ratelimit_wait_ms=0, queue_ms=0)
    store = RunStore(day / "econo-task-a-r1" / "econo.sqlite")
    store.insert("observations", obs_id=1, turn=1, command="ls", host_path="x", bytes=3,
                 sha1="0", cut_in_context=1)
    store.insert("edits", turn=2, predicted_reprocess_R=10, next_call=2, removed_obs="[1]",
                 text="[econo] edit: ...")
    store.insert("status_lines", turn=1, text="[econo] last call ...", stale_paths='["/app/a.py"]')
    store.insert("files", path="/app/a.py", sha1="1", turn_read=3, obs_id=2)
    store.insert("econo_ops", ts=1.0, op="get", args="1")
    store.insert("econo_ops", ts=2.0, op="sql_write", args="CREATE TABLE t(x)")
    return day


def test_scripts_run_on_a_synthetic_day(tmp_path):
    day = make_day(tmp_path)
    runs = load_runs(day)
    assert {r.run_id for r in runs} == {"raw-task-a-r1", "econo-task-a-r1"}

    rows = {r["arm"]: r for r in (results_table.row_for(r) for r in runs)}
    assert rows["econo"]["passed"] == 1 and rows["raw"]["passed"] == 0
    assert rows["raw"]["model_calls"] == 3 and rows["raw"]["cost_usd"] == pytest.approx(0.03)
    assert rows["raw"]["repeated_commands"] == 1 and rows["raw"]["wall_s"] == 300
    assert rows["econo"]["econo_get_on_cut"] == 1 and rows["econo"]["db_writes"] == 1
    assert rows["econo"]["stale_flags"] == 1 and rows["econo"]["stale_flags_reread"] == 1
    md = results_table.markdown(list(rows.values()))
    assert "| Tasks passed | 0 | 1 |" in md

    res = edit_ceiling.ceiling(runs, count=count)
    assert res["arms"]["raw"]["rewrites"] == 1
    raw = res["arms"]["raw"]
    assert raw["format_usd"] + raw["edit_pos_usd"] == pytest.approx(raw["edit_usd"])
    assert "of which: format change" in edit_ceiling.report(res)
    assert res["arms"]["raw"]["edit_usd"] == pytest.approx(7 * (prices.PRICE_IN - prices.PRICE_CACHED))

    qc = quote_check.check(runs, count=count)
    assert qc == [{"run_id": "econo-task-a-r1", "turn": 2, "R": 10, "actual": 7, "rewrite_seen": True,
                   "gap_s": 0.0}]
    assert quote_check.summary(qc)["median_abs_err_tokens"] == 3


def test_split_format_change_vs_edit_position():
    """Built with CLM's own render/parse_back, so the format change is the real one."""
    from clm_harness.context_utils.context_string import parse_back, render_editable

    prev = BASE + tool_turn(90) + tool_turn(60)          # messages 2..5, never edited
    text = render_editable(prev, protect=2)
    # The model edits turn 3 (message 4: the second assistant turn) only.
    edited = text.replace("[[CTX_TURN 3 role=assistant]]\n" + "a" * 10,
                          "[[CTX_TURN 3 role=assistant]]\nshort")
    assert edited != text
    cur = parse_back(edited, prev[:2]) + tool_turn(30)   # CLM appends the next turn
    calls = [{"prompt_tokens": count(prev), "uncached_tokens": 0, "cached_tokens": 0},
             {"prompt_tokens": count(cur), "uncached_tokens": 40 + 150, "cached_tokens": 0}]
    rr = call_rereads([prev, cur], calls, count=count)[0]
    assert rr["rewrite"] and rr["first_change"] == 2     # message 2 lost its tool_calls
    assert rr["text_change"] == 4                        # the real edit
    assert rr["appended"] == 40 and rr["extra_uncached"] == 150
    assert rr["format_uncached"] == 100                  # messages 2..3: 10 + 90 tokens
    assert rr["edit_uncached"] == 50
    per_token = prices.PRICE_IN - prices.PRICE_CACHED
    assert rr["format_usd"] == pytest.approx(100 * per_token)
    assert rr["edit_pos_usd"] == pytest.approx(50 * per_token)


def test_gate_check(tmp_path, capsys):
    from econoclm.analysis import gate_check
    day = make_day(tmp_path)
    assert gate_check.main([str(day), "--gate", "3"]) == 0
    assert "GATE 3: PASS" in capsys.readouterr().out
    # The synthetic econo run saved 1 output for 2 commands and has no get checks.
    assert gate_check.main([str(day), "--gate", "5"]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] saved outputs = commands run: saved 1, commands 2" in out
    assert "[FAIL] econo get byte-identical in sandbox" in out
    assert "[PASS] hook errors = 0" in out
    assert "econo use by the model: {'get': 1, 'sql_write': 1}" in out


def test_gate_check_counts_usage_anomalies(tmp_path, capsys):
    from econoclm.analysis import gate_check
    day = make_day(tmp_path)
    Ledger(day / "gateway.sqlite").insert("raw-task-a-r1", http_status=502, usage_anomaly=1)
    assert gate_check.main([str(day), "--gate", "3"]) == 1
    out = capsys.readouterr().out
    assert "[PASS] gateway calls = CLM LM calls" in out
    assert "[FAIL] usage anomalies = 0: 1 ledger row(s) with usage_anomaly" in out


def test_gate_check_allows_provider_side_retries_only(tmp_path, capsys):
    from econoclm.analysis import gate_check
    day = make_day(tmp_path)
    ledger = Ledger(day / "gateway.sqlite")
    row = dict(prompt_tokens=10, cached_tokens=0, uncached_tokens=10, output_tokens=10,
               reasoning_tokens=0, cost_usd=0.01, http_status=200)
    ledger.insert("raw-task-a-r1", finish_reason="malformed_function_call", **row)
    assert gate_check.main([str(day), "--gate", "3"]) == 0
    assert "CLM 3, provider-side retries 1" in capsys.readouterr().out
    ledger.insert("raw-task-a-r1", finish_reason="stop", **row)  # unexplained extra row
    assert gate_check.main([str(day), "--gate", "3"]) == 1
    assert "[FAIL] gateway calls = CLM LM calls + provider-side retries" in capsys.readouterr().out


def test_rebuild_rejected_is_counted(tmp_path):
    import json as _json
    from econoclm.analysis.common import load_runs
    from econoclm.analysis.results_table import row_for
    day = make_day(tmp_path)
    res = day / "harbor" / "raw-task-a-r1" / "result.json"
    data = _json.loads(res.read_text())
    data["exception_info"] = {"exception_type": "BadRequestError", "exception_message":
        "Error code: 400 - [{'error': {'code': 400, 'message': 'Requests ending with a model "
        "turn are not supported.', 'status': 'INVALID_ARGUMENT'}}]"}
    res.write_text(_json.dumps(data))
    rows = {r.run_id: row_for(r) for r in load_runs(day)}
    assert rows["raw-task-a-r1"]["rebuild_rejected"] == 1
    assert rows["econo-task-a-r1"]["rebuild_rejected"] == 0
