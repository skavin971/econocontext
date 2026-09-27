"""Planner with and without Jev: two tracks, kept apart.

WHAT THE TWO TRACKS ARE
    Track "econo" (default, no Jev)
        For every tool result, the planner uses a FIXED GUESS for p_need_again (the
        probability the agent needs this content again later): 0.3 for a tool
        result, scaled down for results over 2,000 tokens. Config:
        config/econocontext.yaml -> predictor.kind_need_again. Code:
        econocontext/pricing/predictor.py.
    Track "econo+jev" (only with --jev)
        The planner asks Jev instead: econocontext/planner/jev_planner.py.
        That file is EMPTY until someone builds it (instructions inside). Until then a
        --jev run falls back to the fixed guess and records why.

    Either way the number only enters the POINTER option's cost:
        expected re-read cost = p_need_again x full tokens of the tool result.
    Every tool-result decision logs both numbers in the DB (decisions.prediction):
        {"p_need_again": ..., "source": "prior" | "jev" | "prior (jev failed: ...)", "prior": ...}

1. UNIT TESTS (free, no key, no Docker; Jev is faked)
        .venv/bin/python -m pytest tests/test_planner_jev.py -v
   Passing means: the fixed-guess track is unchanged, Jev's number reaches the
   POINTER cost when present, a failing or unbuilt Jev falls back safely, and each
   run records whether Jev was on.

2. ONE REAL JEV CALL (needs TYPESAFE_API_KEY in .env and a built jev_planner.py)
        set -a; . ./.env; set +a
        .venv/bin/python -m pytest -m live tests/test_planner_jev.py -v
   Passing means: Jev answered with a probability between 0 and 1.

3. RUN BOTH TRACKS ON A REAL SWE-BENCH INSTANCE (paid: Gemini; needs Docker)
   Use the same label for both so the report shows them side by side.
        ./scripts/run_econo.sh --label pj1 --set dev --mode observe          # no Jev
        ./scripts/run_econo.sh --label pj1 --set dev --mode observe --jev    # with Jev
        .venv/bin/python scripts/report.py --label pj1
   The report lists "econo" and "econo+jev" separately. To compare the two
   predictions per tool result:
        sqlite3 data/econocontext.sqlite3 "SELECT r.jev, d.prediction FROM decisions d
            JOIN runs r USING(run_id) WHERE d.run_id LIKE 'pj1:%'
            AND d.intercept='admit_tool_result'"
   Observe mode changes nothing the agent does; POINTER is also off in the config,
   so with or without Jev the agent behaves the same. You are comparing predictions.
"""

import os

import pytest

from econocontext.engine import EconoContext
from econocontext.planner import jev_planner
from econocontext.types import HostRequest, ToolResultEvent
from tests.unit.conftest import CONFIG_DIR, FakeHost, conversation

BIG = "\n".join(f"line {i}: " + "x" * 80 for i in range(150))  # ~3,300 tokens: POINTER is offered


@pytest.fixture
def make(tmp_path):
    def build(jev: bool):
        eco = EconoContext(str(CONFIG_DIR), FakeHost(), "run-1", host_name="test", arm="econo",
                           db_path=str(tmp_path / "db.sqlite3"), mode="observe", jev=jev)
        eco.plan_prompt("run-1:root", HostRequest("run-1:root", conversation()))  # task in window
        return eco
    return build


def admit(eco, text=BIG):
    result = eco.admit_tool_result("run-1:root", ToolResultEvent(
        "run-1:root", "c9", "read_file", "k9", text, "/testbed/big.py", ["/testbed/big.py"], False))
    return eco.db.rows("SELECT prediction, candidates FROM decisions WHERE decision_id=?",
                       (result.decision_id,))[0]


def test_without_jev_the_fixed_guess_is_used(make):
    import json
    row = admit(make(jev=False))
    pred = json.loads(row["prediction"])
    tokens = -(-len(BIG) // 4)
    assert pred["source"] == "prior"
    assert pred["p_need_again"] == pytest.approx(0.3 * 2000 / tokens)  # 0.3, scaled for size
    pointer = json.loads(row["candidates"])["POINTER"]
    assert pointer["prepare"] == pytest.approx(pred["p_need_again"] * tokens)


def test_with_jev_its_number_is_used_and_the_guess_is_still_logged(make, monkeypatch):
    import json
    seen = {}

    def fake_jev(tool_result_text, tool_call, task_text):
        seen.update(result=tool_result_text, call=tool_call, task=task_text)
        return 0.9

    monkeypatch.setattr(jev_planner, "p_need_again", fake_jev)
    row = admit(make(jev=True))
    pred = json.loads(row["prediction"])
    assert pred["source"] == "jev" and pred["p_need_again"] == 0.9 and pred["prior"] < 0.3
    assert seen["result"] == BIG and "read_file" in seen["call"] and "Fix the bug" in seen["task"]
    tokens = -(-len(BIG) // 4)
    assert json.loads(row["candidates"])["POINTER"]["prepare"] == pytest.approx(0.9 * tokens)


def test_jev_not_built_yet_falls_back_to_the_guess(make):
    import json
    pred = json.loads(admit(make(jev=True))["prediction"])  # the real, empty jev_planner
    assert pred["source"].startswith("prior (jev failed:") and "not built" in pred["source"]
    assert pred["p_need_again"] == pred["prior"]


def test_a_bad_jev_answer_falls_back_to_the_guess(make, monkeypatch):
    import json
    monkeypatch.setattr(jev_planner, "p_need_again", lambda *a: 7.0)  # not a probability
    pred = json.loads(admit(make(jev=True))["prediction"])
    assert pred["source"].startswith("prior (jev failed:")


def test_each_run_records_whether_jev_was_on(make):
    assert make(jev=False).db.rows("SELECT jev FROM runs")[0]["jev"] == 0


def test_a_jev_run_is_recorded_as_such(make):
    assert make(jev=True).db.rows("SELECT jev FROM runs")[0]["jev"] == 1


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("TYPESAFE_API_KEY"), reason="no TYPESAFE_API_KEY")
def test_one_real_jev_call():
    try:
        p = jev_planner.p_need_again("def parse_items(x):\n    return x\n",
                                     "read_file /testbed/src/app.py",
                                     "Fix the bug in parse_items so it splits on commas.")
    except NotImplementedError:
        pytest.skip("jev_planner.p_need_again is not built yet")
    assert 0.0 <= p <= 1.0
