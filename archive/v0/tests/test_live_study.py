"""The live study's spending guard, tested without spending anything."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research" / "live_study"))

from run import headroom, spent  # noqa: E402


def test_guard_stops_before_a_run_could_cross_the_cap(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    assert headroom(ledger, cap=1.00, per_run=0.40) == 0.40
    ledger.write_text(json.dumps({"cost": 0.35}) + "\n" + json.dumps({"cost": 0.30}) + "\n")
    assert spent(ledger) == pytest.approx(0.65)
    # 0.35 left cannot cover a whole 0.40 run, so none starts.
    assert headroom(ledger, cap=1.00, per_run=0.40) is None
    assert headroom(ledger, cap=1.05, per_run=0.40) == 0.40


def test_unknown_cost_counts_as_zero_only_in_the_sum(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps({"cost": None}) + "\n")
    assert spent(ledger) == 0


def test_a_throttled_run_is_retried_once_and_still_counts(tmp_path):
    from run import THROTTLED, done

    ledger = tmp_path / "ledger.jsonl"
    cell = dict(task="t", model="m", policy="P0", cost=0.10)
    ledger.write_text(json.dumps(dict(cell, reason=f"HTTPStatusError: {THROTTLED}")) + "\n")
    assert ("t", "m", "P0") not in done(ledger)
    with ledger.open("a") as f:
        f.write(json.dumps(dict(cell, reason=f"HTTPStatusError: {THROTTLED}")) + "\n")
    assert ("t", "m", "P0") in done(ledger)
    assert spent(ledger) == pytest.approx(0.20)
