"""Workers on Omnigent: the worker map, RESUME through the policy, and pointers the
gateway keeps applying (shapes recorded 2026-09-29: sys_session_send {agent, args, title})."""

import json
from datetime import datetime, timedelta, timezone

import pytest

import omnigent_layer
from econocontext.monitor import context_map
from econocontext.store.db import AgentDB, now
from omnigent_layer import engine_for, register_run
from omnigent_layer.policy import econocontext
from omnigent_layer.workspace import OmnigentHost

PERMISSIVE = {"allowlist": {"RESUME": True}, "constraints": {"max_quality_risk": 0.2}}
TASK = "Find where src/a.py parses the --lexer flag"


def worker_history(db: AgentDB, run_id: str, title: str = "finder", idle_seconds: float = 60) -> str:
    """A worker that was dispatched with TASK and read src/a.py, `idle_seconds` ago."""
    worker = f"{run_id}:worker:aaaa1111"
    earlier = (datetime.now(timezone.utc) - timedelta(seconds=idle_seconds)).isoformat()
    for agent in (f"{run_id}:root", worker):
        db.execute("INSERT OR IGNORE INTO agents VALUES(?,?,?,?,?,?,?,?)",
                   (agent, run_id, None, None, "idle", None, earlier, earlier))
    db.execute("INSERT INTO runtime_spans (span_id, run_id, agent_id, kind, name, started_at, "
               "status, metadata) VALUES(?,?,?,?,?,?,?,?)",
               ("d0", run_id, f"{run_id}:root", "dispatch", title, earlier, "completed",
                json.dumps({"title": title, "task": TASK})))
    db.execute("INSERT INTO outcomes (outcome_id, run_id, agent_id, phase, uncached_input, "
               "cache_read, output, cost_complete, raw, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
               ("o1", run_id, worker, "agent", 2000, 7000, 50, 1, "{}", earlier))
    for sid, kind, text, role in (
            ("w-task", "task", f"[delegated] {TASK}", "user"),
            ("w-call", "tool_call", 'looking\n[{"args": "{\\"path\\": \\"src/a.py\\"}", '
                                    '"id": "c1", "name": "sys_os_read"}]', "assistant")):
        db.execute("INSERT INTO segments (segment_id, run_id, agent_id, native_id, kind, text, "
                   "tokens, content_hash, role, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (sid, run_id, worker, sid, kind, text, 50, sid, role, earlier))
    db.execute("INSERT INTO segments (segment_id, run_id, agent_id, native_id, kind, text, tokens, "
               "content_hash, role, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
               ("r-a", run_id, f"{run_id}:root", "r-a", "tool_result", "x" * 12000, 3000, "r-a",
                "tool", earlier))
    db.execute("INSERT INTO tool_results VALUES(?,?,?,?,?,?,?,?,?,?)",
               ("r-a", run_id, f"{run_id}:root", "sys_os_read", "k", "r-a",
                json.dumps({"src/a.py": "0"}), 0, 1, earlier))
    return worker


def test_the_worker_map_knows_what_each_worker_holds(tmp_path):
    register_run("m1", "econo", "observe", workdir=str(tmp_path))
    db = AgentDB(omnigent_layer.DB_PATH)
    worker = worker_history(db, "m1", idle_seconds=3600)
    [row] = context_map.workers(db, "m1", ttl_seconds=300)
    assert row["worker_id"] == worker and row["title"] == "finder"
    assert row["files"] == ["src/a.py"] and row["resident_tokens"] == 9000
    assert row["warm"] is False and row["busy"] is False  # its last call was long ago
    assert context_map.file_tokens(db, "m1") == {"src/a.py": 3000}


def dispatch(title: str) -> dict:
    return {"type": "tool_call", "target": "sys_session_send",
            "data": {"name": "sys_session_send",
                     "arguments": {"agent": "worker", "args": f"Now also check {'src/a.py'} tests",
                                   "title": title}}}


def test_autopilot_resumes_the_worker_that_holds_the_file(tmp_path):
    register_run("m2", "econo", "autopilot", overrides=PERMISSIVE, workdir=str(tmp_path))
    worker_history(AgentDB(omnigent_layer.DB_PATH), "m2")
    reply = econocontext("m2", str(tmp_path))(dispatch("new-helper"))
    assert reply["data"]["title"] == "finder" and reply["data"]["args"].startswith("Now also")
    chosen = AgentDB(omnigent_layer.DB_PATH).rows(
        "SELECT chosen, applied FROM decisions WHERE run_id='m2' AND intercept='plan_dispatch'")[0]
    assert (chosen["chosen"], chosen["applied"]) == ("RESUME", 1)


def test_observe_logs_resume_but_changes_nothing(tmp_path):
    register_run("m3", "econo", "observe", overrides=PERMISSIVE, workdir=str(tmp_path))
    worker_history(AgentDB(omnigent_layer.DB_PATH), "m3")
    assert econocontext("m3", str(tmp_path))(dispatch("new-helper")) is None
    row = AgentDB(omnigent_layer.DB_PATH).rows(
        "SELECT chosen, applied FROM decisions WHERE run_id='m3' AND intercept='plan_dispatch'")[0]
    assert (row["chosen"], row["applied"]) == ("RESUME", 0)


def test_autopilot_never_resumes_a_worker_whose_cache_expired(tmp_path):
    register_run("m5", "econo", "autopilot", overrides=PERMISSIVE, workdir=str(tmp_path))
    worker_history(AgentDB(omnigent_layer.DB_PATH), "m5", idle_seconds=3600)
    assert econocontext("m5", str(tmp_path))(dispatch("new-helper")) is None
    row = AgentDB(omnigent_layer.DB_PATH).rows(
        "SELECT chosen FROM decisions WHERE run_id='m5' AND intercept='plan_dispatch'")[0]
    assert row["chosen"] == "FRESH"


@pytest.mark.parametrize("idle_seconds", [3600, 1])  # cold, or still busy: its lines are held either way
def test_autopilot_hands_off_the_lines_a_worker_read(tmp_path, idle_seconds):
    from econocontext.evidence import EvidenceEvent, make_ref
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("".join(f"line {i}\n" for i in range(1, 101)))
    run = f"m6-{idle_seconds}"
    register_run(run, "econo", "autopilot", workdir=str(tmp_path), overrides={
        "allowlist": {"HANDOFF": True}, "constraints": {"max_quality_risk": 0.2},
        "placement": {"force": "HANDOFF"}})
    db = AgentDB(omnigent_layer.DB_PATH)
    worker = worker_history(db, run, idle_seconds=idle_seconds)
    ref = make_ref("file", "src/a.py", "sha256:old", "line 10", "L10-12")
    db.add_evidence_events(run, worker, 1, [EvidenceEvent("acquired", "sys_os_read", "k", "src/a.py", ref)])
    reply = econocontext(run, str(tmp_path))(dispatch("new-helper"))
    given = reply["data"]["args"]
    assert reply["data"]["title"] == "new-helper" and given.startswith("Now also check src/a.py tests")
    assert "src/a.py lines 10-12:" in given and "line 11" in given and "line 13" not in given
    row = db.rows("SELECT chosen, applied FROM decisions WHERE run_id=? AND intercept='plan_dispatch'",
                  (run,))[0]
    assert (row["chosen"], row["applied"]) == ("HANDOFF", 1)


def test_every_dispatch_gets_its_own_span_even_from_a_new_evaluator(tmp_path):
    register_run("m7", "econo", "observe", workdir=str(tmp_path))
    econocontext("m7", str(tmp_path))(dispatch("first"))
    econocontext("m7", str(tmp_path))(dispatch("second"))  # Omnigent built a new evaluator
    titles = [r[0] for r in AgentDB(omnigent_layer.DB_PATH).rows(
        "SELECT name FROM runtime_spans WHERE run_id='m7' AND kind='dispatch' ORDER BY started_at")]
    assert titles == ["first", "second"]


def test_pointer_files_land_in_the_workspace(tmp_path):
    register_run("m4", "econo", "observe", workdir=str(tmp_path))
    engine, _ = engine_for("m4")
    assert isinstance(engine.host, OmnigentHost) and engine.caps.pointer and engine.caps.resume
    segment = engine.db.segment("missing") or type("S", (), {"id": "abc" * 6, "text": "full text"})()
    path = engine.host.pointer_store.materialize(segment)
    assert (tmp_path / path).read_text() == "full text" and path.startswith(".econocontext/")
