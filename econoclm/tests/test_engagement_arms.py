"""The tool-engagement arms v1.1 / v1.2 (REPORT.md deviation 8): exact cut tags, notes,
and that the arms differ from v1 and from each other only where intended."""

import subprocess
from pathlib import Path

import yaml

from econoclm.arms.econo_clm.hooks import EconoHooks, missing_lines
from econoclm.bench.tblite import run, run_arms

ARMS = Path(__file__).parents[1] / "arms"


def clm_cut(output: str, max_chars: int, head: int, tail: int) -> str:
    """CLM's ContextEnv.truncate_observation (head/tail kept, middle elided)."""
    if len(output) <= max_chars:
        return output
    return f"HEAD\n{output[:head]}\n---- elided ----\nTAIL\n{output[-tail:]}"


def test_missing_lines_match_what_clm_elides():
    stdout = "".join(f"line {i:04d} " + "x" * 40 + "\n" for i in range(1, 501))
    a, b = missing_lines(stdout, "", max_chars=2000, obs_cfg={"head_chars": 1000, "tail_chars": 1000})
    lines = stdout.splitlines()
    shown = clm_cut(stdout.rstrip(), 2000, 1000, 1000)
    # Every line strictly inside the range is absent from what the model saw...
    assert all(lines[i - 1] not in shown for i in range(a + 1, b))
    # ...and the boundary lines are the partly shown ones.
    assert lines[a - 1][:5] in shown and lines[a - 2] in shown and lines[b] in shown
    assert (a, b) == (20, 481)


def test_missing_lines_default_cut_and_stderr_offset():
    assert missing_lines("short", "", max_chars=10000) is None
    err = "\n".join(f"e{i}" for i in range(1, 4001))
    a, b = missing_lines("", err, max_chars=10000)          # head/tail 5000 each
    # The saved output is "" + "\n" + stderr: one extra first line.
    assert a == err[:5000].count("\n") + 2


def test_cut_tag_names_lines_only_when_head_and_tail_were_shown(tmp_path):
    h = EconoHooks(tmp_path / "run", "econo11-t-r1", observation_max_chars=2000, protect=lambda: 2,
                   state_dir="/tmp/s", cut_lines=True, obs_cfg={"head_chars": 1000, "tail_chars": 1000})

    class R:
        stdout = "".join(f"line {i:04d} " + "x" * 40 + "\n" for i in range(1, 501))
        stderr = ""

    class SR:
        stdout_block = clm_cut(R.stdout.rstrip(), 2000, 1000, 1000)

    assert h.cut_tag(3, R, SR, True) == "[obs 3] lines 20-481 not shown: econo get 3 20-481"
    SR.stdout_block = "budget cut it again"
    assert h.cut_tag(3, R, SR, True) == "[obs 3] (cut in context; full: econo get 3)"
    h.cut_lines = False
    SR.stdout_block = clm_cut(R.stdout.rstrip(), 2000, 1000, 1000)
    assert h.cut_tag(3, R, SR, True) == "[obs 3] (cut in context; full: econo get 3)"   # v1
    assert h.cut_tag(4, R, SR, False) == "[obs 4]"


def test_econo_note_and_notes(tmp_path):
    script = ARMS / "econo_clm_v11/skill/econo_db/econo"
    (tmp_path / "obs").mkdir()
    sh = lambda *a: subprocess.run(["sh", str(script), *a], capture_output=True, text=True,  # noqa: E731
                                   env={"ECONO_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert sh("notes").stdout == "(no notes yet)\n"
    assert sh("note", "path is /app/src/parse.py").stdout == "saved note 1\n"
    assert sh("note", "tab\there\nnewline").stdout == "saved note 2\n"
    assert sh("notes").stdout == "1. path is /app/src/parse.py\n2. tab here newline\n"
    assert sh("note").returncode == 1
    ops = [line.split("\t")[1] for line in (tmp_path / "log.tsv").read_text().splitlines()]
    assert ops == ["notes", "note", "note", "notes"]   # usage errors are not logged (as in v1)


def test_arms_differ_only_where_intended():
    v1 = yaml.safe_load((ARMS / "econo_clm/config.yaml").read_text())
    v11 = yaml.safe_load((ARMS / "econo_clm_v11/config.yaml").read_text())
    v12 = yaml.safe_load((ARMS / "econo_clm_v12/config.yaml").read_text())
    strip = lambda c: {**c, "agent": None, "agent_kwargs": {**c["agent_kwargs"], "skill_dirs": None}}  # noqa: E731
    assert strip(v1) == strip(v11) == strip(v12)
    assert v11["agent"] == v12["agent"] == "econoclm.arms.econo_clm_v11.agent:EconoClmV11Agent"
    t11 = (ARMS / "econo_clm_v11/skill/econo_db/econo").read_bytes()
    assert t11 == (ARMS / "econo_clm_v12/skill/econo_db/econo").read_bytes()
    s11 = (ARMS / "econo_clm_v11/skill/econo_db/SKILL.md").read_text()
    s12 = (ARMS / "econo_clm_v12/skill/econo_db/SKILL.md").read_text()
    assert s12.startswith(s11) and s12[len(s11):].count("\n- ") == 3
    assert "4,096 tokens" in s11 and "near the start" not in s12


def test_run_arms_registers_without_touching_run_py(tmp_path):
    run_arms.install()
    try:
        run_id, argv = run.build_command("econo11", "task-a", 1, tblite=tmp_path, out_dir=tmp_path,
                                         port=8787)
        assert run_id == "econo11-task-a-r1"
        assert f"econo_run_dir={tmp_path / run_id}" in argv
        assert any("econo_clm_v11/skill/econo_db" in a for a in argv)
        _, argv12 = run.build_command("econo12", "task-a", 1, tblite=tmp_path, out_dir=tmp_path,
                                      port=8787)
        assert any("econo_clm_v12/skill/econo_db" in a for a in argv12)
        _, raw = run.build_command("raw", "task-a", 1, tblite=tmp_path, out_dir=tmp_path, port=8787)
        assert not any(a.startswith("econo_run_dir=") for a in raw)
    finally:
        run.build_command = run_arms._build_command
        for k in run_arms.EXTRA_ARMS:
            run.ARMS.pop(k, None)
