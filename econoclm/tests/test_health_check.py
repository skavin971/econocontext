"""Health check: seeded order and swap logic (no Docker: checks are injected)."""

import json
import random
import subprocess
import sys

from econoclm.bench.tblite.health_check import Check, heal, oracle_command, read_result
from econoclm.bench.tblite.select_tasks import SEED, seeded_order, select

TASKS = [f"task-{i:03d}" for i in range(100)]


def test_selection_is_the_prefix_of_the_seeded_order():
    assert select(TASKS) == random.Random(SEED).sample(sorted(TASKS), 10)
    assert seeded_order(TASKS)[:10] == select(TASKS)
    assert sorted(seeded_order(TASKS)) == TASKS


def test_failures_are_swapped_for_the_next_seeded_tasks():
    order = ["a", "b", "c", "d", "x", "y", "z"]
    bad = {"b", "x"}                       # b fails, its replacement x fails too
    healthy, checks, swaps = heal(["a", "b", "c", "d"], order,
                                  lambda t: Check(t, reward=0.0 if t in bad else 1.0), workers=2)
    assert healthy == ["a", "y", "c", "d"]  # b's slot: x (failed), then y
    assert swaps == [("b", "x"), ("x", "y")]
    assert [c.task for c in checks] == ["a", "b", "c", "d", "x", "y"]


def test_runs_out_of_spares():
    healthy, _, swaps = heal(["a", "b"], ["a", "b"], lambda t: Check(t, reward=0.0))
    assert healthy == [] and swaps == []


def test_read_result(tmp_path):
    assert read_result(tmp_path) == (None, "no result.json")
    (tmp_path / "result.json").write_text(json.dumps({"verifier_result": {"rewards": {"reward": 1.0}}}))
    assert read_result(tmp_path) == (1.0, None)
    (tmp_path / "result.json").write_text(json.dumps(
        {"exception_info": {"exception_type": "BuildError", "exception_message": "apt failed"}}))
    assert read_result(tmp_path) == (None, "BuildError: apt failed")


def test_oracle_command_calls_no_model(tmp_path):
    argv = oracle_command("t", tblite=tmp_path, out_dir=tmp_path, harbor="harbor")
    assert argv[argv.index("-a") + 1] == "oracle"
    assert "-m" not in argv and not any("api_base" in a for a in argv)


def test_dry_run_cli():
    out = subprocess.run([sys.executable, "-m", "econoclm.bench.tblite.health_check", "--dry-run"],
                         capture_output=True, text=True, check=True).stdout.strip().splitlines()
    assert len(out) == 10 and all(" -a oracle " in ln for ln in out)
