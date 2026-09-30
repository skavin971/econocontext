"""The perfect-future oracle: only turns that did nothing but read are claimed."""

import time

import pytest

from econocontext.evidence import EvidenceEvent, make_ref
from econocontext.oracle.evidence_schedule import (MIXED, NOT_REMOVABLE, STRICT, classify,
                                                   headroom, schedule)
from econocontext.store.db import AgentDB
from econocontext.types import ProviderUsage

READ = {"name": "read_file", "kind": "file"}
GREP = {"name": "grep_search", "kind": "search"}
TOPIC = {"name": "update_topic", "kind": "meta"}
EDIT = {"name": "replace", "kind": "write"}
AGENT = {"name": "invoke_agent", "kind": "other"}


def test_classes():
    assert classify([READ, GREP], False) == STRICT
    assert classify([TOPIC, READ], False) == STRICT      # narration does not count
    assert classify([READ], True) == MIXED
    assert classify([READ, EDIT], False) == NOT_REMOVABLE
    assert classify([AGENT], False) == NOT_REMOVABLE      # delegation is never claimed
    assert classify([TOPIC], False) == classify([], True) == NOT_REMOVABLE


def run(tmp_path, calls):
    """calls: [(context_key, response_calls, response_text, cost_usd, evidence acquired)]"""
    db = AgentDB(tmp_path / "db.sqlite3")
    db.start_run("r", "omnigent:gemini", None, "econo", "observe", "m", 1.0, "f")
    for call_no, (key, response, text, cost, acquired) in enumerate(calls):
        span = f"s{call_no}"
        db.add_runtime_span(span, "r", "r:root", "model", "m", f"c{call_no}", None,
                            {"call_no": call_no, "context_key": key})
        db.finish_runtime_span(span, 10.0, "completed",
                               {"response_calls": response, "response_text": text})
        db.add_outcome(f"c{call_no}", "r", "r:root", None, "agent",
                       ProviderUsage(1, 0, None, 1), 1.0, cost, True, "p")
        db.add_evidence_events("r", "r:root", call_no, acquired)
        time.sleep(0.002)  # spans are ordered by start time
    return schedule(db, "r", usd_per_input_token=0.001)


def evidence(path):
    ref = make_ref("file", path, "sha256:1", "x " * 50)
    return EvidenceEvent("acquired", "read_file", path, path, ref)


def test_a_read_only_turn_is_removable_and_charged_for_its_evidence(tmp_path):
    rows = run(tmp_path, [("root", [READ], False, 0.10, []),
                          ("root", [EDIT], False, 0.20, [evidence("a.py")]),
                          ("root", [], True, 0.05, [])])
    first = rows[0]
    assert (first["class"], first["consumer"], first["removable_calls"]) == (STRICT, 1, 1)
    assert [e["source_key"] for e in first["evidence"]] == ["a.py"]
    assert first["estimated_saved_usd"] == pytest.approx(0.10 - first["tokens_added"] * 0.001)
    assert [r["class"] for r in rows[1:]] == [NOT_REMOVABLE, NOT_REMOVABLE]
    total = headroom(rows)
    assert (total["calls"], total["strict"], total["removable_calls"]) == (3, 1, 1)
    assert total["share_of_cost"] == pytest.approx(first["estimated_saved_usd"] / 0.35)


def test_mixed_turns_are_reported_but_never_claimed(tmp_path):
    rows = run(tmp_path, [("root", [GREP], True, 0.10, []), ("root", [], True, 0.1, [evidence("a.py")])])
    assert rows[0]["class"] == MIXED and rows[0]["evidence"]
    assert rows[0]["removable_calls"] == 0 and rows[0]["estimated_saved_usd"] == 0


def test_the_consumer_is_the_next_call_of_the_same_conversation(tmp_path):
    # A sub-agent's call (context 'child') comes between the root's read and its result.
    rows = run(tmp_path, [("root", [READ], False, 0.1, []),
                          ("child", [GREP], False, 0.1, []),
                          ("root", [], True, 0.1, [evidence("a.py")]),
                          ("child", [], True, 0.1, [evidence("b.py")])])
    assert (rows[0]["consumer"], [e["source_key"] for e in rows[0]["evidence"]]) == (2, ["a.py"])
    assert (rows[1]["consumer"], [e["source_key"] for e in rows[1]["evidence"]]) == (3, ["b.py"])
    assert rows[3]["consumer"] is None


def test_a_turn_without_a_price_still_counts_as_removable(tmp_path):
    rows = run(tmp_path, [("root", [READ], False, None, []), ("root", [], True, 0.1, [evidence("a.py")])])
    assert rows[0]["removable_calls"] == 1 and rows[0]["estimated_saved_usd"] is None
    total = headroom(rows)
    assert (total["strict"], total["strict_unpriced"], total["estimated_saved_usd"]) == (1, 1, 0)
