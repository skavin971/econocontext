from econocontext.store import retrieval
from econocontext.store.db import AgentDB
from econocontext.types import SegmentKind
from tests.unit.conftest import conversation, seg


def db_with_run(tmp_path):
    db = AgentDB(tmp_path / "db.sqlite3")
    db.start_run("run-1", "test", None, "econo", "observe", "m", 1.0, "fp")
    return db


def test_segments_round_trip_and_are_write_once(tmp_path):
    from econocontext.monitor.registry import Registry
    db = db_with_run(tmp_path)
    reg = Registry(db, "run-1")
    window = reg.sync_window("run-1:root", conversation())
    back = db.window("run-1:root")
    assert [s.id for s in back] == [s.id for s in window]
    assert back[3].text == window[3].text and back[3].role == "tool"
    reg.sync_window("run-1:root", conversation())  # the same identities again
    assert db.rows("SELECT COUNT(*) AS n FROM segments")[0]["n"] == 4


def test_keyword_search_finds_by_path_and_identifier(tmp_path):
    from econocontext.monitor.registry import Registry
    db = db_with_run(tmp_path)
    reg = Registry(db, "run-1")
    reg.sync_window("run-1:root", conversation())
    hits = retrieval.search(db, "run-1", "look at src/app.py", exclude_in_window=False)
    assert any("parse_items" in h.text for h in hits)
    hits = retrieval.search(db, "run-1", "where is parse_items defined", exclude_in_window=False)
    assert hits and "parse_items" in hits[0].text
    # Excluding the window hides content the agent already sees.
    assert retrieval.search(db, "run-1", "parse_items", agent_id="run-1:root") == []


def test_a_write_invalidates_dependent_results(tmp_path):
    from econocontext.monitor.registry import Registry
    db = db_with_run(tmp_path)
    Registry(db, "run-1").ensure_agent("run-1:root")
    s = seg("run-1:root", "c9", SegmentKind.TOOL_RESULT, "contents", role="tool")
    db.add_segment(s)
    db.add_tool_result("t1", "run-1", "run-1:root", "read_file", "k", s.id, {"/a.py": "0"}, False)
    db.add_tool_result("t2", "run-1", "run-1:root", "read_file", "k2", s.id, {"/b.py": "0"}, False)
    db.add_stored_result("r1", "run-1", "task", "run-1:root", "answer", {"/a.py": "0"}, False)
    db.bump("run-1", ["/a.py"])
    assert db.find_tool_result("run-1", "read_file", "k") is None
    assert db.find_tool_result("run-1", "read_file", "k2") is not None
    assert db.find_stored_result("run-1", "task") is None
