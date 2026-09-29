"""learn/: labels from a finished trace, the learned predictors, and counterfactual replay.

The trace is built by hand so every expected number is known: one agent, 10 model
calls (t=00..09), two file reads arriving at t=02. Big A is quoted by the agent at
t=05 (needed again); big B is never used again.
"""

import json

import pytest

from econocontext import config
from econocontext.learn.labels import distinctive_lines, label_run
from econocontext.learn.predictors import History, role
from econocontext.learn.replay import replay_run
from econocontext.store.db import AgentDB

RUN, AGENT = "r1", "r1:root"
LINE_A = "def lexer_for_upload(contents): return 'text'  # the fix"
BIG_A = "\n".join([LINE_A] + [f"filler line {i} " + "a" * 60 for i in range(120)])
BIG_B = "\n".join(f"unrelated line {i} " + "b" * 60 for i in range(120))


def t(i: int) -> str:
    return f"2026-09-29T10:00:{i:02d}+00:00"


def build(db: AgentDB, run_id=RUN, instance="inst-1"):
    agent = f"{run_id}:root"
    db.start_run(run_id, "test", instance, "econo", "observe", "m", 1.0, "fp")
    db.execute("INSERT INTO agents VALUES(?,?,?,?,?,?,?,?)",
               (agent, run_id, None, None, "idle", None, t(0), t(0)))
    for i in range(10):
        db.execute("INSERT INTO outcomes (outcome_id, run_id, agent_id, decision_id, phase, "
                   "uncached_input, cache_read, output, cost_complete, raw, created_at) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (f"{run_id}-o{i}", run_id, agent, None, "agent", 1000, 3000, 50, 1, "{}", t(i)))
    for name, text, path in (("a", BIG_A, "src/a.py"), ("b", BIG_B, "src/b.py")):
        seg = f"{run_id}-seg-{name}"
        db.execute("INSERT INTO segments (segment_id, run_id, agent_id, native_id, kind, text, "
                   "tokens, content_hash, role, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (seg, run_id, agent, name, "tool_result", text, len(text) // 4, name, "tool",
                    t(2) + name))
        db.execute("INSERT INTO tool_results VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (seg, run_id, agent, "sys_os_read", f"key-{name}", seg,
                    json.dumps({path: "0"}), 0, 1, t(2) + name))
        db.execute("INSERT INTO decisions (decision_id, run_id, agent_id, intercept, mode, "
                   "candidates, feasible, chosen, applied, predicted_cost, why_not, decision_ms, "
                   "created_at, subject_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (f"{run_id}-d-{name}", run_id, agent, "admit_tool_result", "observe", "{}",
                    "[]", "KEEP_FULL", 0, "{}", "[]", 0.1, t(2) + name, seg))
    db.execute("INSERT INTO segments (segment_id, run_id, agent_id, native_id, kind, text, tokens, "
               "content_hash, role, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (f"{run_id}-quote", run_id, agent, "q", "message",
                f"The bug is here: {LINE_A}", 20, "q", "assistant", t(5)))


@pytest.fixture
def db(tmp_path):
    return AgentDB(tmp_path / "db.sqlite3")


def test_labels_mark_the_quoted_result_needed_and_the_ignored_one_not(db):
    build(db)
    assert label_run(db, RUN)["needed_again"] == 1
    rows = {r["subject_id"]: r for r in db.rows("SELECT * FROM labels WHERE kind='result'")}
    a, b = rows[f"{RUN}-seg-a"], rows[f"{RUN}-seg-b"]
    assert json.loads(a["needed_calls"]) == [5] and a["referenced"] == 1 and a["refetched"] == 0
    assert json.loads(b["needed_calls"]) == []
    assert a["arrived_call"] == 3  # calls t=00, 01, 02 happened before it arrived
    h = db.rows("SELECT h_actual FROM labels WHERE kind='decision' AND subject_id=?",
                (f"{RUN}-d-a",))[0]["h_actual"]
    assert h == 7  # calls t=03..09 came after the decision


def test_distinctive_lines_skip_short_and_boilerplate_lines():
    lines = distinctive_lines('{"content": "x = 1\\n' + LINE_A + '", "limit": 2000}')
    assert lines == [LINE_A]
    assert distinctive_lines("exit code: 0 and some more words to be long") == []


def test_predictors_learn_from_other_tasks_only(db):
    build(db, "r1", "inst-1")
    build(db, "r2", "inst-2")
    for run in ("r1", "r2"):
        label_run(db, run)
    history = History(db, exclude_instance="inst-1")
    assert history.runs == ["r2"]
    assert history.h_hat("r3:root", 4) == 6  # 10 calls in r2, 4 made so far
    p, source = history.p_hat("sys_os_read", 0)
    assert p == pytest.approx((1 + 1) / (2 + 2)) and source.startswith("history")
    assert role("r1:worker:ab12cd34") == "worker" and role("r1:root") == "root"


def test_empty_history_falls_back_to_the_prior(db):
    history = History(db)
    assert history.p_hat("sys_os_read") == (0.3, "prior")
    assert history.h_hat("r9:root", 5) == 13  # the config guess: 18 - 5


def test_replay_points_out_only_what_was_not_needed_again(db):
    build(db)
    label_run(db, RUN)
    cfg = config.load("config", {"constraints": {"max_quality_risk": 0.2}})
    out = replay_run(db, RUN, cfg, allow={"POINTER"})
    chosen = {row["tokens"]: row["oracle"] for row in out["rows"]}
    tokens_b = len(BIG_B) // 4
    assert chosen[tokens_b] == "POINTER" and out["changed"]["oracle"] == 1
    # The oracle saving is B's residency: (full - pointer) x (1 + H) in model units.
    assert out["saving_nu"]["oracle"] > 0
    assert out["saving_nu_cache_adjusted"]["oracle"] < out["saving_nu"]["oracle"]
    assert out["cache_share"] == pytest.approx(0.75)
