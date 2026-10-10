"""The policy maps Omnigent tool events (shapes recorded 2026-09-28) to engine calls."""

import subprocess

import omnigent_layer
from econocontext.store.db import AgentDB
from omnigent_layer import register_run
from omnigent_layer.policy import econocontext


def event(phase, name, args, result=None):
    if phase == "tool_call":
        return {"type": "tool_call", "target": name, "data": {"name": name, "arguments": args}}
    return {"type": "tool_result", "target": name, "data": {"result": result},
            "request_data": {"name": name, "arguments": args}}


def repo(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "a.py").write_text("x = 1\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t",
                                                  "commit", "-qm", "base"]):
        subprocess.run(["git", "-C", str(work), *cmd], check=True)
    return work


def test_reads_are_stored_and_an_edit_bumps_exactly_that_path(tmp_path):
    work = repo(tmp_path)
    register_run("r1", "econo", "observe")
    policy = econocontext("r1", str(work))
    assert policy(event("tool_call", "sys_os_read", {"path": "a.py"})) is None
    assert policy(event("tool_result", "sys_os_read", {"path": "a.py"}, "x = 1\n")) is None
    policy(event("tool_call", "sys_os_edit", {"path": "a.py"}))
    (work / "a.py").write_text("x = 2\n")
    policy(event("tool_result", "sys_os_edit", {"path": "a.py"}, "ok"))
    db = AgentDB(omnigent_layer.DB_PATH)
    read = db.rows("SELECT valid FROM tool_results WHERE run_id='r1' AND tool_name='sys_os_read'")
    assert read[0]["valid"] == 0  # the file it read changed, so it may not be reused
    assert db.current_versions("r1")["a.py"] == "1"
    intercepts = [r[0] for r in db.rows("SELECT intercept FROM decisions WHERE run_id='r1'")]
    assert intercepts.count("before_tool_call") == 2 and intercepts.count("admit_tool_result") == 2


def test_baseline_runs_and_failures_abstain(tmp_path):
    register_run("b1", "baseline", "observe")
    assert econocontext("b1")(event("tool_call", "sys_os_read", {"path": "a"})) is None
    assert econocontext("never-registered")({"type": "tool_result", "data": "garbage"}) is None
    assert not AgentDB(omnigent_layer.DB_PATH).rows("SELECT * FROM decisions")
