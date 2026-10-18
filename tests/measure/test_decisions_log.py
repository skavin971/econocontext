"""EconoContext's decisions as the readers see them (measure/report.py decisions_log): decisions.jsonl from
step 12a on, an older run's session.sqlite3 otherwise, the same rows either way."""

import json
import sqlite3

from measure.report import decisions_log


def test_the_jsonl_log_and_an_old_session_database_read_the_same(tmp_path):
    rows = [("4 arrival", "preview", {"source": "jev", "needed_again": 0.1}),
            ("6 evict", "evict", {"source": "prior (TimeoutError)"}), ("9 invalidate", "bump", None)]
    new, old = tmp_path / "new", tmp_path / "old"
    new.mkdir(), old.mkdir()
    (new / "decisions.jsonl").write_text("".join(
        json.dumps({"at": 1.0, "call_no": 3, "rule": r, "item_id": None, "action": a, "forced": False,
                    "prices": None, "answers": ans, "jev_usage": None, "note": None}) + "\n" for r, a, ans in rows))
    with sqlite3.connect(old / "session.sqlite3") as db:
        db.execute("CREATE TABLE decisions (id INTEGER PRIMARY KEY, at REAL, call_no INTEGER, rule TEXT, item_id INTEGER,"
                   " action TEXT, forced INTEGER, prices TEXT, answers TEXT, jev_usage TEXT, note TEXT)")
        db.executemany("INSERT INTO decisions (rule, action, answers) VALUES (?,?,?)",
                       [(r, a, json.dumps(ans) if ans else None) for r, a, ans in rows])
    pick = lambda log: [(d["rule"], d["action"], d["answers"]) for d in log]   # noqa: E731
    assert pick(decisions_log(new)) == pick(decisions_log(old)) == rows
    assert decisions_log(tmp_path / "none") == [] and decisions_log(None) == []
