"""Cross-session memory on the Agent DB: ingesting Tier A sessions, recalling at epoch 0, the
pool as the experimental condition, and the harness policy's hooks."""

import json
import sqlite3
import time

import pytest

from agent_db.memory import MemoryPolicy, Pool, ingest_session, recall, tool_key

SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY, call_no INTEGER, agent TEXT, tool TEXT, key TEXT, input TEXT,
  output TEXT, tokens INTEGER, form TEXT, shown_tokens INTEGER, in_context INTEGER DEFAULT 1,
  epoch INTEGER, created REAL);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY, at REAL, call_no INTEGER, rule TEXT, item_id INTEGER, action TEXT,
  forced INTEGER, prices TEXT, answers TEXT, jev_usage TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
"""
READ = ("read_file", {"path": "src/app.py"}, "def parse_items(x):\n    return x\n")
LS = ("bash", {"command": "ls src"}, "app.py\nutil.py\n")
WRITE = ("write_file", {"path": "src/app.py", "content": "fixed"}, "written")
READ_AFTER = ("read_file", {"path": "src/app.py"}, "fixed")


def session_file(path, calls):
    """A Tier A session.sqlite3 with these calls in order; writes bump the epoch like Owner does."""
    db = sqlite3.connect(path)
    db.executescript(SESSION_SCHEMA)
    epoch = 0
    for n, (tool, args, output) in enumerate(calls):
        if tool == "write_file":
            epoch += 1
            db.execute("INSERT INTO decisions (at, call_no, rule, action) VALUES (?,?,?,?)",
                       (time.time(), n, "9 invalidate", "bump"))
        db.execute("INSERT INTO items (call_no, agent, tool, key, input, output, tokens, form, "
                   "shown_tokens, in_context, epoch, created) VALUES (?,?,?,?,?,?,?,?,?,1,?,?)",
                   (n, "main", tool, tool_key(tool, args), json.dumps(args), output,
                    len(output) // 4, "full", len(output) // 4, epoch, time.time()))
    db.execute("INSERT INTO state VALUES ('epoch', ?)", (json.dumps(epoch),))
    db.commit()
    db.close()
    return path


@pytest.fixture
def store(contract_db):
    return contract_db


def test_ingest_records_calls_with_content_identity(store, tmp_path):
    session_file(tmp_path / "r1.sqlite3", [READ, LS, WRITE, READ_AFTER])
    counts = ingest_session(store, tmp_path / "r1.sqlite3", "tc.base.task-a.r1", passed=True)
    assert counts["items"] == 4 and counts["task"] == "task-a"
    run = store.get_run("tc.base.task-a.r1")
    assert run["instance_id"] == "task-a" and run["resolved"] is True and run["host"] == "tier-a"
    results = store.tool_results_with_text("tc.base.task-a.r1")   # in call order
    assert [r["tool_name"] for r in results] == ["read_file", "bash", "write_file", "read_file"]
    assert results[0]["args_key"] == results[3]["args_key"] == tool_key(*READ[:2])  # same call, twice
    before = results[0]["read_set"]
    assert list(before) == ["src/app.py"] and before["src/app.py"].startswith("sha256:")
    assert results[1]["read_set"] == {"*": "image:task-a"}
    assert results[2]["read_set"] == {"*": "image:task-a"} and results[2]["side_effect"] is True
    after = results[3]["read_set"]
    assert after["src/app.py"].startswith("text:") and after["*"] == "epoch:1"
    assert store.mutations("tc.base.task-a.r1") == 1
    evidence = store.evidence_of("tc.base.task-a.r1")
    assert {e["source_kind"] for e in evidence} == {"file", "search"}


def test_recall_serves_only_provable_matches(store, tmp_path):
    session_file(tmp_path / "r1.sqlite3", [READ, LS, WRITE, READ_AFTER])
    ingest_session(store, tmp_path / "r1.sqlite3", "tc.base.task-a.r1", passed=True)
    pool = Pool(labels=["tc"])
    hit = recall(store, pool, "task-a", *READ[:2], epoch=0)
    assert hit and hit.output == READ[2] and hit.run_id == "tc.base.task-a.r1"
    assert recall(store, pool, "task-a", *LS[:2], epoch=0).output == LS[2]
    # the same read after a write in THIS session: not provable
    assert recall(store, pool, "task-a", *READ[:2], epoch=1) is None
    # a write is never served
    assert recall(store, pool, "task-a", *WRITE[:2], epoch=0) is None
    # a call the earlier session made only after its own write: its read set is per-session
    assert recall(store, pool, "task-a", "read_file", {"path": "src/app.py", "description": "x"},
                  epoch=0).output == READ[2]  # the description is not part of the identity
    # another task: nothing is provably identical
    assert recall(store, pool, "task-b", *READ[:2], epoch=0) is None
    # the pool is the condition
    assert recall(store, Pool(labels=["other"]), "task-a", *READ[:2], epoch=0) is None
    assert recall(store, Pool(labels=["tc"], only_passed=True), "task-a", *READ[:2], epoch=0)
    ingest_session(store, session_file(tmp_path / "r2.sqlite3", [READ]), "tc.base.task-a.r2",
                   passed=False)
    assert recall(store, Pool(labels=["tc"], only_passed=True, exclude={"tc.base.task-a.r1"}),
                  "task-a", *READ[:2], epoch=0) is None


class Inner:
    """A stand-in for Base or Econo: records the hook calls it receives."""
    name, budget = "inner", "allowed"

    def __init__(self):
        self.log = []

    def start(self, instruction, harness):
        self.log.append(("start", instruction))

    async def before_call(self, messages, harness):
        return messages

    def before_tool(self, name, args):
        return None

    def after_tool(self, name, args, output, served, call_id=None):
        self.log.append(("after", name, served))
        return output

    def report(self):
        return {"inner": "ok"}


class Harness:
    def __init__(self, run_id):
        self.run_id, self.model = run_id, "qwen"


def test_memory_policy_serves_from_an_earlier_session_and_reports_provenance(store, tmp_path):
    ingest_session(store, session_file(tmp_path / "r1.sqlite3", [READ, LS]), "tc.base.task-a.r1")
    inner = Inner()
    policy = MemoryPolicy(db=store, labels=["tc"], inner=inner)
    policy.start("Fix it.", Harness("tc.memory.task-a.r2"))
    assert inner.log == [("start", "Fix it.")]

    served = policy.before_tool(*READ[:2])
    assert served == READ[2]
    assert policy.after_tool(*READ[:2], served, True) == READ[2]
    assert inner.log[-1] == ("after", "read_file", True)

    assert policy.before_tool(*WRITE[:2]) is None            # writes run, and bump the epoch
    policy.after_tool(*WRITE[:2], "written", False)
    assert policy.before_tool(*LS[:2]) is None               # after a write: nothing is served

    report = policy.report()
    assert report["served_calls"] == 1 and report["served"][0]["source_run"] == "tc.base.task-a.r1"
    assert report["inner"] == {"inner": "ok"} and report["pool"]["same_task"] is True
    # this session was recorded too, and a third session could draw on it
    assert store.get_run("tc.memory.task-a.r2")["status"] == "done"
    assert recall(store, Pool(labels=["tc"]), "task-a", *READ[:2], epoch=0)
