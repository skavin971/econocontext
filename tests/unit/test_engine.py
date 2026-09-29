"""Each intercept end to end through the engine, on plain data."""

import hashlib

import pytest

from econocontext.types import (DispatchIntent, DispatchResult, HostRequest, ProviderUsage,
                                ToolCallEvent, ToolResultEvent)
from tests.unit.conftest import FakeHost, conversation


def key(*parts):
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def test_observe_mode_logs_but_always_returns_the_host_default(engine):
    eco = engine(mode="observe")
    req = HostRequest("run-1:root", conversation())
    rendered = eco.plan_prompt("run-1:root", req)
    assert not rendered.applied and [s.id for s in rendered.segments] == [s.id for s in req.segments]
    rows = eco.db.rows("SELECT intercept, applied, mode FROM decisions")
    assert [tuple(r) for r in rows] == [("plan_prompt", 0, "observe")]


def test_answer_from_store_is_byte_identical_and_only_when_unchanged(engine):
    eco = engine(mode="autopilot")
    call = ToolCallEvent("run-1:root", "c1", "read_file", {"file_path": "/a.py"}, key("a"), False)
    assert eco.before_tool_call("run-1:root", call).run_tool  # nothing stored yet
    text = "line one\n  line two\t\n"
    eco.admit_tool_result("run-1:root", ToolResultEvent(
        "run-1:root", "c1", "read_file", key("a"), text, "/a.py", ["/a.py"], False))
    again = eco.before_tool_call("run-1:root", ToolCallEvent(
        "run-1:root", "c2", "read_file", {"file_path": "/a.py"}, key("a"), False))
    assert not again.run_tool and again.stored_text == text  # byte-identical, no label
    eco.on_file_write("run-1:root", "/a.py")
    after = eco.before_tool_call("run-1:root", ToolCallEvent(
        "run-1:root", "c3", "read_file", {"file_path": "/a.py"}, key("a"), False))
    assert after.run_tool  # the file changed: the stored answer is no longer valid


def test_pointer_is_applied_only_when_allowed(engine, tmp_path):
    host = FakeHost()
    eco = engine(mode="autopilot", host=host)
    eco.cfg["allowlist"]["POINTER"] = True
    eco.config.raw["constraints"]["max_quality_risk"] = 1.0
    big = "\n".join(f"line {i}: " + "x" * 80 for i in range(400))
    res = eco.admit_tool_result("run-1:root", ToolResultEvent(
        "run-1:root", "c1", "execute", key("t"), big, None, ["*"], True))
    assert res.rendered_text != big and "saved at /tmp/econocontext/" in res.rendered_text
    assert host.saved and next(iter(host.saved.values())) == big  # full text reopenable


def test_reuse_result_returns_the_stored_result_and_fresh_runs_the_host(engine):
    eco = engine(mode="autopilot")
    intent = DispatchIntent("run-1:root", "t1", "general-purpose", "find parse_items", key("x"))
    calls = []

    def run_default():
        calls.append(1)
        eco.registry.ensure_agent("run-1:task:t1", "run-1:root", "general-purpose")
        eco.registry.record_read("run-1:task:t1", {"/a.py": "0"})
        return DispatchResult("parse_items is at src/app.py:1", "run-1:task:t1")

    first = eco.plan_dispatch(intent, run_default)
    second = eco.plan_dispatch(intent, run_default)
    assert not first.reused and second.reused and len(calls) == 1
    assert second.result_text == first.result_text  # byte-identical
    eco.on_file_write("run-1:root", "/a.py")
    third = eco.plan_dispatch(intent, run_default)
    assert not third.reused and len(calls) == 2


def test_record_writes_usage_outcomes_and_a_report(engine):
    eco = engine()
    rendered = eco.plan_prompt("run-1:root", HostRequest("run-1:root", conversation()))
    eco.record("run-1:root", rendered.decision_id,
               ProviderUsage(1000, 9000, None, 200, reasoning=150,
                             raw={"input_tokens": 10000}, cache_write_applicable=False),
               outcome_id="call-1")
    report = eco.report()
    assert report["totals"]["calls"] == 1 and report["totals"]["cache_read_share"] == 0.9
    assert report["predicted_vs_actual"]["calls"] == 1
    assert report["feasible_counts"]["AS_IS"] == 1
    assert report["totals"]["cost_usd"] == pytest.approx(0.002175)
    assert report["totals"]["cost_complete"]


def test_a_failing_component_fails_open(engine, monkeypatch):
    eco = engine(mode="autopilot")
    monkeypatch.setattr("econocontext.engine.select",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("optimizer down")))
    req = HostRequest("run-1:root", conversation())
    rendered = eco.plan_prompt("run-1:root", req)
    assert not rendered.applied and [s.id for s in rendered.segments] == [s.id for s in req.segments]
    row = eco.db.rows("SELECT chosen, error FROM decisions")[0]
    assert row["chosen"] == "HOST_DEFAULT" and "optimizer down" in row["error"]
