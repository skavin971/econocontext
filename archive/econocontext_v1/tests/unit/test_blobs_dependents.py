"""The storage additions: content-addressed blobs, dependents(run, source), snapshot(run).
Existing behavior (versions, invalidation, write-once segments) must not change."""

import json
import sqlite3

import pytest

from econocontext.engine import make_segment
from econocontext.store.blobs import blob_key
from econocontext.store.db import SCHEMA, AgentDB
from econocontext.types import AgentNode, SegmentKind

RUN = "r1"
BIG = "\n".join(f"line {i}: " + "x" * 80 for i in range(100))  # ~8.8 KB, over the blob threshold


@pytest.fixture
def db(tmp_path):
    d = AgentDB(tmp_path / "db.sqlite3")
    d.start_run(RUN, "test", "inst", "econo", "observe", "m", 1.0, "fp")
    for agent in (f"{RUN}:root", f"{RUN}:worker:aaaa1111"):
        d.upsert_agent(RUN, AgentNode(agent, None, None))
    return d


def seg(agent, native, kind, text, **kw):
    return make_segment(RUN, agent, native, kind, text, role=kw.pop("role", "tool"), **kw)


def test_identical_blobs_are_stored_once(db):
    data = BIG.encode()
    assert db.blobs.put(data) == db.blobs.put(data) == blob_key(data)
    assert db.rows("SELECT COUNT(*) n FROM blobs")[0]["n"] == 1
    assert db.blobs.get(blob_key(data)) == data
    assert db.blobs.put(b"other") != blob_key(data)
    with pytest.raises(KeyError):
        db.blobs.get("0" * 64)


def test_large_segments_share_one_blob_and_keep_their_text_and_hash(db):
    root = seg(f"{RUN}:root", "t1", SegmentKind.TOOL_RESULT, BIG)
    worker = seg(f"{RUN}:worker:aaaa1111", "t9", SegmentKind.TOOL_RESULT, BIG)
    small = seg(f"{RUN}:root", "t2", SegmentKind.TOOL_RESULT, "short")
    for s in (root, worker, small):
        db.add_segment(s)
    rows = {r["segment_id"]: r for r in db.rows("SELECT segment_id, blob_key, text, content_hash "
                                                "FROM segments")}
    assert rows[root.id]["blob_key"] == rows[worker.id]["blob_key"] == blob_key(BIG.encode())
    assert rows[small.id]["blob_key"] is None
    assert rows[root.id]["text"] == BIG and rows[root.id]["content_hash"] == root.content_hash
    assert db.rows("SELECT COUNT(*) n FROM blobs")[0]["n"] == 1
    assert db.segment(root.id).text == BIG  # reads are unchanged


def test_large_stored_results_get_a_blob_key(db):
    db.add_stored_result("s1", RUN, "task", f"{RUN}:worker:aaaa1111", BIG, {"src/a.py": "0"}, False)
    assert db.rows("SELECT blob_key FROM stored_results")[0]["blob_key"] == blob_key(BIG.encode())


def reads(path: str) -> str:
    return 'reading\n' + json.dumps([{"id": "c", "name": "sys_os_read",
                                     "args": json.dumps({"path": path})}])


def test_dependents_finds_what_a_changed_source_affects(db):
    for sid, path in (("ta", "src/a.py"), ("tb", "src/b.py")):
        s = seg(f"{RUN}:root", sid, SegmentKind.TOOL_RESULT, f"content of {path}")
        db.add_segment(s)
        db.add_tool_result(sid, RUN, f"{RUN}:root", "sys_os_read", f"k-{sid}", s.id,
                           {path: "0"}, False)
    db.add_stored_result("sa", RUN, "task-a", f"{RUN}:worker:aaaa1111", "answer",
                         {"src/a.py": "0"}, False)
    db.add_segment(seg(f"{RUN}:worker:aaaa1111", "c1", SegmentKind.TOOL_CALL, reads("src/a.py"),
                       role="assistant"))
    db.add_segment(seg(f"{RUN}:root", "c2", SegmentKind.TOOL_CALL, reads("src/a.py"),
                       role="assistant"))  # the root is not a worker

    assert db.dependents(RUN, "src/a.py") == dict(tool_results=["ta"], stored_results=["sa"],
                                                  workers=[f"{RUN}:worker:aaaa1111"])
    assert db.dependents(RUN, "./src/a.py")["tool_results"] == ["ta"]  # paths are normalized
    assert db.dependents(RUN, "src/c.py") == dict(tool_results=[], stored_results=[], workers=[])

    db.bump(RUN, ["src/a.py"])  # existing behavior: exactly the dependents go stale
    valid = {r["tool_result_id"]: r["valid"] for r in db.rows("SELECT tool_result_id, valid "
                                                             "FROM tool_results")}
    assert valid == {"ta": 0, "tb": 1}
    assert db.rows("SELECT valid FROM stored_results")[0]["valid"] == 0


def test_snapshot_matches_the_individual_reads(db):
    window = [seg(f"{RUN}:root", n, SegmentKind.MESSAGE, n, role="user") for n in ("a", "b")]
    for i, s in enumerate(window):
        db.add_segment(s)
        s.position = i
    db.set_window(f"{RUN}:root", window)
    db.bump(RUN, ["src/a.py"])
    snap = db.snapshot(RUN)
    assert snap.run["run_id"] == RUN and snap.versions == db.current_versions(RUN)
    assert snap.windows[f"{RUN}:root"] == [s.id for s in window]
    assert {a["agent_id"] for a in snap.agents} == {f"{RUN}:root", f"{RUN}:worker:aaaa1111"}


def old_schema() -> str:
    """Today's schema as it was before blobs: no blobs table, no blob_key columns."""
    text = SCHEMA.read_text()
    start = text.index("-- Content-addressed storage")
    text = text[:start] + text[text.index(");", start) + 2:]
    lines = [line for line in text.splitlines() if "blob_key" not in line]
    return "\n".join(lines).replace("created_at          TEXT NOT NULL,\n);",
                                     "created_at          TEXT NOT NULL\n);")


def test_a_database_from_before_blobs_opens_unchanged(tmp_path):
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(old_schema())
    assert "blob_key" not in {r[1] for r in conn.execute("PRAGMA table_info(segments)")}
    conn.execute("INSERT INTO runs (run_id, host, arm, mode, model, temperature, "
                 "config_fingerprint, started_at) VALUES('old','h','econo','observe','m',1,'f','t')")
    conn.commit()
    conn.close()
    db = AgentDB(path)  # adds the new columns and the blobs table; keeps every row
    assert "blob_key" in {r[1] for r in db.conn.execute("PRAGMA table_info(segments)")}
    assert "blob_key" in {r[1] for r in db.conn.execute("PRAGMA table_info(stored_results)")}
    assert db.rows("SELECT run_id FROM runs")[0]["run_id"] == "old"
    assert db.blobs.get(db.blobs.put(b"after upgrade")) == b"after upgrade"
