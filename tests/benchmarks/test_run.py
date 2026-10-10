"""The TBLite runner (benchmarks/tblite/run.py): a trial starts only if the gateway's daily budget
still holds a full run's calls after reserving them for every running trial. No Harbor, no network."""

import importlib.util
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
    monkeypatch.setattr(module, "run_trial", lambda *args: started.append(args[2]) or {"ran": True})
    return module, started


def budget(used, per_run=150, per_day=8000):
    return lambda gateway: {"requests_today": used, "max_requests_per_day": per_day, "max_calls_per_run": per_run}


def test_a_trial_starts_while_a_full_run_still_fits_in_the_daily_budget(runner, monkeypatch):
    module, started = runner
    monkeypatch.setattr(module, "health", budget(used=8000 - 150))
    assert module.trial(SimpleNamespace(label="L", arm="raw", gateway="g"), Path("."), "t", 1) == {"ran": True}
    assert started == ["t"] and module.running == [0]


def test_no_trial_starts_when_a_run_could_be_cut_off(runner, monkeypatch):
    module, started = runner
    monkeypatch.setattr(module, "health", budget(used=8000 - 149))
    result = module.trial(SimpleNamespace(label="L", arm="raw", gateway="g"), Path("."), "t", 1)
    assert "daily budget" in result["skipped"] and started == []


def test_running_trials_reserve_a_full_run_each(runner, monkeypatch):
    module, started = runner
    monkeypatch.setattr(module, "health", budget(used=8000 - 250))
    module.running[0] = 1                       # one trial still running: 150 reserved, 100 left
    result = module.trial(SimpleNamespace(label="L", arm="raw", gateway="g"), Path("."), "t", 1)
    assert "daily budget" in result["skipped"] and started == []
