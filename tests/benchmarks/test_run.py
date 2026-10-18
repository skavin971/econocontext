"""The TBLite runner (benchmarks/tblite/run.py): the order of trials, and the gates that stop a run
cleanly between trials (daily budget, battery, invalid calls, crashes, Jev fallbacks). No Harbor, no
network: the gateway's health, the battery, the trial itself and the measurement are replaced."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runner(monkeypatch):
    spec = importlib.util.spec_from_file_location("tblite_run", ROOT / "benchmarks" / "tblite" / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    started = []
    monkeypatch.setattr(module, "run_trial", lambda a, log_dir, arm, task, repeat, run_id, out:
                        started.append((arm, task)) or {"run_id": run_id, "arm": arm, "task": task, "repeat": repeat})
    monkeypatch.setattr(module, "battery", lambda: None)
    monkeypatch.setattr(module, "health", budget(used=0))
    return module, started


def budget(used, per_run=150, per_day=8000):
    return lambda gateway, wait_s=0: {"requests_today": used, "max_requests_per_day": per_day,
                                      "max_calls_per_run": per_run}


def args(**kw):
    return SimpleNamespace(**{"label": "L", "gateway": "g", "gates": False, "progress": None, "budget": None, **kw})


def test_trials_are_interleaved_by_repeat_then_task_then_arm(runner):
    module, _ = runner
    jobs = module.jobs_for(["raw", "clm-32k"], ["a", "b"], 1, 2)
    assert jobs == [("raw", "a", 1), ("clm-32k", "a", 1), ("raw", "b", 1), ("clm-32k", "b", 1),
                    ("raw", "a", 2), ("clm-32k", "a", 2), ("raw", "b", 2), ("clm-32k", "b", 2)]


def test_a_trial_starts_while_a_full_run_still_fits_in_the_daily_budget(runner, monkeypatch):
    module, started = runner
    monkeypatch.setattr(module, "health", budget(used=8000 - 150))
    assert module.trial(args(), Path("."), "base", "t", 1)["run_id"] == "L.base.t.r1"
    assert started == [("base", "t")] and module.running == [0] and module.stop["reason"] is None


def test_the_run_stops_before_a_trial_that_could_be_cut_off(runner, monkeypatch):
    module, started = runner
    monkeypatch.setattr(module, "health", budget(used=8000 - 149))
    assert "daily budget" in module.trial(args(), Path("."), "base", "t", 1)["skipped"]
    monkeypatch.setattr(module, "health", budget(used=0))       # later trials are not started either
    assert "stopped: daily budget" in module.trial(args(), Path("."), "base", "u", 1)["skipped"] and started == []


def test_running_trials_reserve_a_full_run_each(runner, monkeypatch):
    module, started = runner
    monkeypatch.setattr(module, "health", budget(used=8000 - 250))
    module.running[0] = 1                       # one trial still running: 150 reserved, 100 left
    assert "daily budget" in module.trial(args(), Path("."), "base", "t", 1)["skipped"] and started == []


def test_a_low_battery_stops_the_run_and_a_charging_one_does_not(runner, monkeypatch):
    module, started = runner
    monkeypatch.setattr(module, "battery", lambda: (21, True))
    module.trial(args(), Path("."), "base", "t", 1)
    monkeypatch.setattr(module, "battery", lambda: (15, False))    # charging (on power): no stop
    module.trial(args(), Path("."), "base", "u", 1)
    monkeypatch.setattr(module, "battery", lambda: (20, True))
    assert "battery at 20%" in module.trial(args(), Path("."), "base", "v", 1)["skipped"]
    assert started == [("base", "t"), ("base", "u")]


def gate(module, monkeypatch, summary, totals):
    monkeypatch.setattr(module, "measure", lambda s: totals)
    module.check(args(gates=True), {"run_id": "L.x.t.r1", "arm": "raw", "task": "t", "repeat": 1, "calls": 5,
                                    "reward": 1.0, "exception": None, "overflow": False, **summary})


def test_three_invalid_calls_in_a_trial_stop_the_run(runner, monkeypatch):
    module, _ = runner
    gate(module, monkeypatch, {}, {"invalid": 2, "valid": 3})
    assert module.stop["reason"] is None
    gate(module, monkeypatch, {}, {"invalid": 3, "valid": 2})
    assert "3 invalid calls" in module.stop["reason"]


def test_an_arm_crashing_twice_in_a_row_stops_the_run_but_an_overflow_is_not_a_crash(runner, monkeypatch):
    module, _ = runner
    crash = {"reward": None, "exception": "RuntimeError"}
    gate(module, monkeypatch, crash, {})
    gate(module, monkeypatch, {"reward": None, "exception": "BadRequestError", "overflow": True}, {})
    gate(module, monkeypatch, crash, {})
    assert module.stop["reason"] is None          # the overflow (a graded FAIL) broke the streak
    gate(module, monkeypatch, crash, {})
    assert "crashed in 2 trials in a row" in module.stop["reason"]


def test_jev_falling_back_on_more_than_half_its_questions_stops_the_run(runner, monkeypatch):
    module, _ = runner
    gate(module, monkeypatch, {"arm": "econo+jev", "jev_fallbacks": {"asked": 4, "fell_back": 2}}, {})
    assert module.stop["reason"] is None
    gate(module, monkeypatch, {"arm": "econo+jev", "jev_fallbacks": {"asked": 4, "fell_back": 3}}, {})
    assert "Jev fell back on 3 of 4" in module.stop["reason"]


def test_with_a_budget_every_arm_is_named_after_it(runner):
    module, _ = runner
    assert [module.arm_name(arm, 32_000) for arm in ("base", "clm")] == ["base-32k", "clm-32k"]
    assert (module.arm_name("base", 30_500), module.arm_name("base", None)) == ("base-30500", "base")
    summary = module.trial(args(budget=32_000), Path("logs"), "base", "acl", 1)
    assert summary["run_id"] == "L.base-32k.acl.r1"


def test_econocontext_with_a_budget_and_clm_without_one_are_refused(runner, monkeypatch):
    module, _ = runner
    base = ["run.py", "--label", "L", "--tasks", "acl", "--model", "qwen3.8:27b"]
    monkeypatch.setattr(sys, "argv", base + ["--policy", "base,econo+jev", "--budget", "32000"])
    with pytest.raises(SystemExit, match="EconoContext has no budget mechanism yet"):
        module.main()
    monkeypatch.setattr(sys, "argv", base + ["--policy", "clm"])
    with pytest.raises(SystemExit, match="the clm arm needs --budget"):
        module.main()


def test_the_summary_arm_needs_a_budget_and_a_cut_summary_is_counted_not_a_stop(runner, monkeypatch):
    module, _ = runner
    monkeypatch.setattr(sys, "argv", ["run.py", "--label", "L", "--tasks", "acl", "--model", "m", "--policy", "summary"])
    with pytest.raises(SystemExit, match="the summary arm needs --budget"):
        module.main()
    assert module.arm_name("summary", 32_000) == "summary-32k"
    monkeypatch.setattr(module, "measure", lambda summary: {"valid": 9, "invalid": 0})
    module.check(args(gates=True), {"run_id": "L.summary-32k.acl.r1", "arm": "summary-32k", "task": "acl", "repeat": 1,
                                    "calls": 9, "reward": 1.0, "compactions": [{"G": 2048, "capped": True, "P_after": 900}]})
    assert module.stop["reason"] is None


def test_a_clm_trial_over_our_limit_is_flagged_not_stopped(runner, monkeypatch, tmp_path):
    module, _ = runner
    monkeypatch.setattr(module, "measure", lambda summary: {"valid": 9, "invalid": 0})
    log = tmp_path / "progress.md"
    for peak, flagged in ((29_952, False), (29_953, True)):
        module.check(args(gates=True, budget=32_000, progress=str(log)),
                     {"run_id": "L.clm-32k.acl.r1", "arm": "clm-32k", "task": "acl", "repeat": 1, "calls": 9,
                      "reward": 1.0, "peak_P": peak})
        assert ("FLAG" in log.read_text()) == flagged
    assert module.stop["reason"] is None


def test_the_directed_policy_refuses_to_run(runner, monkeypatch):
    module, _ = runner
    monkeypatch.setattr(sys, "argv", ["run.py", "--label", "L", "--tasks", "acl", "--model", "m", "--policy", "directed"])
    with pytest.raises(SystemExit, match="the directed policy refuses to run"):
        module.main()
