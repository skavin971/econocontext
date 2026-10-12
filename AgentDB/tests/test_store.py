"""The kept operations (CHANGES.md), on Postgres from pgserver: runs, write-once segments and blobs, the
window with its forms and token counts, tool results, versions and invalidation."""

import psycopg
import pytest

from agent_db import Form, PostgresAgentDB, SegmentKind, make_segment
from agent_db.postgres import BLOB_MIN_BYTES

RUN = "run-1"          # the run the db fixture starts (conftest.py)


def seg(native, text, kind=SegmentKind.MESSAGE, role="user", tokens=None, **kw):
    return make_segment(RUN, native, kind, text, len(text.split()) if tokens is None else tokens, role=role, **kw)


def placed(s, position, part=0, form=Form.FULL):
    s.position, s.part, s.form = position, part, form
    return s


def test_a_run_holds_its_task_and_a_run_id_is_used_once(db):
    assert db.get_run(RUN) == {"run_id": RUN, "task": "Fix the bug."}
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.start_run(RUN, "again")


def test_segments_are_write_once_and_keep_the_callers_token_count(db):
    s = seg("m1", "hello there", tokens=7)
    db.add_segment(s)
    db.add_segment(s)
    assert db.rows("SELECT COUNT(*) AS n FROM segments")[0]["n"] == 1
    got = db.segment(s.id)
    assert (got.text, got.tokens, got.role, got.kind) == ("hello there", 7, "user", SegmentKind.MESSAGE)
    assert seg("m1", "hello there").id == s.id and seg("m2", "hello there").id != s.id


def test_large_texts_also_go_to_blobs(db):
    big = "x" * BLOB_MIN_BYTES
    db.add_segment(seg("m1", big))
    key = db.rows("SELECT blob_key FROM segments")[0]["blob_key"]
    assert key and db.blobs.get(key) == big.encode()


def test_the_window_is_set_whole_with_positions_parts_forms_and_tokens(db):
    system, task = seg("m0", "sys", role="system", tokens=3), seg("m1", "task", tokens=4)
    call = seg("c1", '{"name": "bash"}', kind=SegmentKind.TOOL_CALL, role="assistant", pair_id="c1", tokens=5)
    say = seg("m2", "", role="assistant", tokens=0)
    result = seg("c1", "preview text", kind=SegmentKind.TOOL_RESULT, role="tool", pair_id="c1", tokens=6)
    for s in (system, task, call, say, result):
        db.add_segment(s)
    db.set_window(RUN, [placed(system, 0), placed(task, 1), placed(say, 2), placed(call, 2, 1),
                        placed(result, 3, form=Form.PREVIEW)])
    got = db.window(RUN)
    assert [(s.position, s.part, s.form, s.text) for s in got] == [
        (0, 0, Form.FULL, "sys"), (1, 0, Form.FULL, "task"), (2, 0, Form.FULL, ""),
        (2, 1, Form.FULL, '{"name": "bash"}'), (3, 0, Form.PREVIEW, "preview text")]
    assert [r["tokens"] for r in db.rows("SELECT tokens FROM window_entries ORDER BY position, part")] == [3, 4, 0, 5, 6]
    db.set_window(RUN, [placed(system, 0), placed(task, 1)])          # the window becomes exactly these
    assert [s.text for s in db.window(RUN)] == ["sys", "task"]


def test_a_form_outside_the_six_is_refused(db):
    s = seg("m0", "sys")
    db.add_segment(s)
    s.position, s.form = 0, "POINTER"
    with pytest.raises(ValueError):
        db.set_window(RUN, [s])
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO window_entries VALUES (%s,%s,0,0,true,'POINTER',1,now())", (RUN, s.id))


def test_tool_results_are_found_latest_valid_and_side_effect_free(db):
    out1, out2 = seg("c1", "a\nb", role="tool", pair_id="c1"), seg("c2", "a\nb\nc", role="tool", pair_id="c2")
    write = seg("c3", "wrote 3 bytes", role="tool", pair_id="c3")
    for s in (out1, out2, write):
        db.add_segment(s)
    db.add_tool_result("1", RUN, "bash", "k-cat", {"command": "cat a"}, out1.id, {"*": "0"}, False)
    db.add_tool_result("2", RUN, "bash", "k-cat", {"command": "cat a"}, out2.id, {"*": "0"}, False)
    db.add_tool_result("3", RUN, "write_file", "k-w", {"path": "a"}, write.id, {"*": "0"}, True)
    found = db.find_tool_result(RUN, "bash", "k-cat")
    assert (found["tool_result_id"], found["text"], found["tokens"]) == ("2", "a\nb\nc", 3)
    assert db.find_tool_result(RUN, "write_file", "k-w") is None          # a write is never reused


def test_a_bump_advances_the_workspace_and_invalidates_what_was_read_before(db):
    out = seg("c1", "a", role="tool", pair_id="c1")
    db.add_segment(out)
    db.add_tool_result("1", RUN, "bash", "k", {"command": "cat a"}, out.id, {"*": "0"}, False)
    assert db.current_versions(RUN) == {}
    db.bump(RUN, [])
    db.bump(RUN, [])
    assert db.current_versions(RUN) == {"*": "2"}
    assert db.find_tool_result(RUN, "bash", "k") is None
    later = seg("c2", "a", role="tool", pair_id="c2")
    db.add_segment(later)
    db.add_tool_result("2", RUN, "bash", "k", {"command": "cat a"}, later.id, {"*": "2"}, False)
    assert db.find_tool_result(RUN, "bash", "k")["tool_result_id"] == "2"


def test_window_results_join_each_shown_segment_to_its_tool_result(db):
    full = seg("c1", "line\n" * 50, kind=SegmentKind.TOOL_RESULT, role="tool", pair_id="c1", tokens=100)
    shown = seg("c1", "[preview]", kind=SegmentKind.TOOL_RESULT, role="tool", pair_id="c1", tokens=3)
    call = seg("c1", '{"name": "bash"}', kind=SegmentKind.TOOL_CALL, role="assistant", pair_id="c1")
    other = seg("c9", "rejected", kind=SegmentKind.TOOL_RESULT, role="tool", pair_id="c9")   # no tool result
    for s in (full, shown, call, other):
        db.add_segment(s)
    db.add_tool_result("1", RUN, "read_file", "k", {"path": "a.py"}, full.id, {"*": "0"}, False)
    db.set_window(RUN, [placed(call, 2, 1), placed(shown, 3, form=Form.PREVIEW), placed(other, 4)])
    [row] = db.window_results(RUN)
    assert (row["tool_result_id"], row["args"], row["form"], row["tokens"], row["full_tokens"], row["position"]) == \
        ("1", {"path": "a.py"}, "PREVIEW", 3, 100, 3)
    assert row["full_text"] == full.text and row["text"] == "[preview]" and row["valid"]


def test_runs_do_not_see_each_others_windows_or_results(db, pg_uri):
    other = PostgresAgentDB(pg_uri)
    other.start_run("run-2", "Another task.")
    a = seg("m0", "mine")
    b = make_segment("run-2", "m0", SegmentKind.MESSAGE, "theirs", 1, role="user")
    db.add_segment(a)
    other.add_segment(b)
    db.set_window(RUN, [placed(a, 0)])
    other.set_window("run-2", [placed(b, 0)])
    other.bump("run-2", [])
    assert [s.text for s in db.window(RUN)] == ["mine"] and db.current_versions(RUN) == {}
    other.close()


def test_the_store_holds_no_cost(db):
    columns = {r["column_name"] for r in db.rows(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='public'")}
    assert not [c for c in columns if any(w in c for w in ("cost", "price", "usd", "flop", "bill"))]
