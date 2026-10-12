"""AGENT_DB_CONTRACT.md, Part B, as executable checks.

`db` runs on both backends: these are the operations the SQLite reference implements, so
Postgres must agree with it. `postgres` alone covers the operations the contract names
that the reference only has as SQL in its callers; the expected values come from that SQL.
"""

import threading

import pytest

from agent_db.records import (AgentNode, AgentStatus, Candidate, CostBreakdown, Decision,
                              EvidenceEvent, Intercept, OperatorType, ProviderUsage, Rejection,
                              SegmentKind, make_ref, sha256)

from tests.conftest import ROOT, RUN, WORKER, J, conversation, peek, reads, seg, tick

BIG = "\n".join(f"line {i}: " + "x" * 80 for i in range(100))  # over the 4,096-byte threshold


def usage(uncached=100, cached=50, output=20):
    return ProviderUsage(uncached_input=uncached, cache_read=cached, cache_write=0, output=output,
                         reasoning=5, latency_ms=12.5, raw={"prompt_tokens": uncached + cached})


def decision(did, subject, intercept=Intercept.ADMIT_TOOL_RESULT, chosen="POINTER"):
    costs = {"KEEP_FULL": CostBreakdown(10, 0, 0, 40, 1), "POINTER": CostBreakdown(2, 0, 1, 8, 1)}
    return Decision(id=did, intercept=intercept,
                    chosen=Candidate(chosen, OperatorType.APPROXIMATE), cost=costs[chosen],
                    rejected=[Rejection(n, "costlier") for n in costs if n != chosen],
                    feasible=list(costs), candidate_costs=costs, applied=True,
                    subject_id=subject, payloads={chosen: {"tokens": 5}})


# ---------------------------------------------------------------- both backends

def test_a_run_is_registered_once_and_closed(db):
    db.start_run(RUN, "other", None, "baseline", "measure", "m2", 0.5, "fp2")  # ignored
    db.end_run(RUN, "done")
    db.set_resolved(RUN, True)
    row = peek(db, "SELECT host, arm, status, resolved, ended_at FROM runs WHERE run_id=?",
               "MATCH (r:Run {run_id: $a}) RETURN r.host AS host, r.arm AS arm, r.status AS status, "
               "r.resolved AS resolved, r.ended_at AS ended_at", (RUN,))
    assert (row["host"], row["arm"], row["status"]) == ("test", "econo", "done")
    assert bool(row["resolved"]) is True and row["ended_at"]


def test_segments_are_write_once_and_the_window_round_trips(db):
    window = conversation()
    for i, s in enumerate(window):
        db.add_segment(s)
        s.position = i
    db.set_window(ROOT, window)
    back = db.window(ROOT)
    assert [s.id for s in back] == [s.id for s in window]
    assert back[3].text == window[3].text and back[3].role == "tool" and back[3].position == 3
    for s in window:
        db.add_segment(s)  # the same identities again: no new rows
    assert db.segment(window[0].id).kind == SegmentKind.SYSTEM
    db.set_window(ROOT, window[:2])  # the window shrinks; the segments stay
    assert [s.id for s in db.window(ROOT)] == [s.id for s in window[:2]]
    assert db.segment(window[3].id) is not None


def test_identical_blobs_are_stored_once_and_large_text_gets_a_key(db):
    data = BIG.encode()
    assert db.blobs.put(data) == db.blobs.put(data) == sha256(data)
    assert db.blobs.get(sha256(data)) == data
    with pytest.raises(KeyError):
        db.blobs.get("0" * 64)
    big = seg(ROOT, "t1", SegmentKind.TOOL_RESULT, BIG)
    small = seg(ROOT, "t2", SegmentKind.TOOL_RESULT, "short")
    db.add_segment(big)
    db.add_segment(small)
    assert db.segment(big.id).text == BIG
    db.add_stored_result("s1", RUN, "task", WORKER, BIG, {"src/a.py": "0"}, False)
    found = db.find_stored_result(RUN, "task")
    assert found["result_text"] == BIG and J(found["read_set"]) == {"src/a.py": "0"}


def test_a_write_invalidates_exactly_the_dependents(db):
    s = seg(ROOT, "c9", SegmentKind.TOOL_RESULT, "contents")
    db.add_segment(s)
    db.add_tool_result("t1", RUN, ROOT, "read_file", "k", s.id, {"/a.py": "0"}, False)
    db.add_tool_result("t2", RUN, ROOT, "read_file", "k2", s.id, {"/b.py": "0"}, False)
    db.add_tool_result("t3", RUN, ROOT, "write_file", "k3", s.id, {"/b.py": "0"}, True)
    db.add_stored_result("r1", RUN, "task", WORKER, "answer", {"/a.py": "0"}, False)
    assert db.find_tool_result(RUN, "read_file", "k")["text"] == "contents"
    assert db.find_tool_result(RUN, "write_file", "k3") is None  # side effects never reuse

    db.bump(RUN, ["/a.py"])

    assert db.current_versions(RUN) == {"/a.py": "1", "*": "1"}
    assert db.find_tool_result(RUN, "read_file", "k") is None
    assert db.find_tool_result(RUN, "read_file", "k2") is not None
    assert db.find_stored_result(RUN, "task") is None
    db.bump(RUN, [])  # only the workspace epoch moves; /b.py is untouched
    assert db.current_versions(RUN)["*"] == "2"
    assert db.find_tool_result(RUN, "read_file", "k2") is not None


def test_dependents_finds_what_a_changed_source_affects(db):
    for sid, path in (("ta", "src/a.py"), ("tb", "src/b.py")):
        s = seg(ROOT, sid, SegmentKind.TOOL_RESULT, f"content of {path}")
        db.add_segment(s)
        db.add_tool_result(sid, RUN, ROOT, "sys_os_read", f"k-{sid}", s.id, {path: "0"}, False)
        tick()
    db.add_stored_result("sa", RUN, "task-a", WORKER, "answer", {"src/a.py": "0"}, False)
    db.add_segment(seg(WORKER, "c1", SegmentKind.TOOL_CALL, reads("src/a.py"), role="assistant"))
    db.add_segment(seg(ROOT, "c2", SegmentKind.TOOL_CALL, reads("src/a.py"), role="assistant"))
    assert db.dependents(RUN, "src/a.py") == dict(tool_results=["ta"], stored_results=["sa"],
                                                  workers=[WORKER])
    assert db.dependents(RUN, "./src/a.py")["tool_results"] == ["ta"]
    assert db.dependents(RUN, "src/c.py") == dict(tool_results=[], stored_results=[], workers=[])


def test_snapshot_matches_the_individual_reads(db):
    window = [seg(ROOT, n, SegmentKind.MESSAGE, n, role="user") for n in ("a", "b")]
    for i, s in enumerate(window):
        db.add_segment(s)
        s.position = i
    db.set_window(ROOT, window)
    db.add_tool_result("t1", RUN, ROOT, "read_file", "k", window[0].id, {"/a.py": "0"}, False)
    db.bump(RUN, ["/x.py"])
    snap = db.snapshot(RUN)
    assert snap.run["run_id"] == RUN and snap.versions == db.current_versions(RUN)
    assert snap.windows[ROOT] == [s.id for s in window]
    assert {a["agent_id"] for a in snap.agents} == {ROOT, WORKER}
    assert [r["tool_result_id"] for r in snap.valid_tool_results] == ["t1"]
    assert snap.taken_at


def test_agents_upsert_updates_status(db):
    db.upsert_agent(RUN, AgentNode(WORKER, ROOT, "worker", status=AgentStatus.IDLE))
    agents = {a["agent_id"]: a for a in db.snapshot(RUN).agents}
    assert agents[WORKER]["status"] == "idle" and agents[WORKER]["parent_id"] == ROOT


def test_outcomes_count_and_cache_share(db):
    assert db.cache_share(RUN) is None and db.calls_so_far(ROOT) == 0
    db.add_outcome("o1", RUN, ROOT, None, "agent", usage(100, 50), 1.0, 0.001, True, "promo")
    db.add_outcome("o1", RUN, ROOT, None, "agent", usage(999, 0), 9.0, 0.9, True, "promo")  # ignored
    tick()
    s = seg(ROOT, "later", SegmentKind.MESSAGE, "after the first call", role="user")
    db.add_segment(s)
    tick()
    db.add_outcome("o2", RUN, ROOT, None, "agent", usage(100, 100), 1.0, 0.001, True, "promo")
    assert db.calls_so_far(ROOT) == 2 and db.cache_share(RUN) == pytest.approx(150 / 350)
    assert db.call_count_at(ROOT, s.id) == 1


def test_runtime_spans_open_and_finish(db):
    db.add_runtime_span("sp1", RUN, ROOT, "model", "chat", "n1", None, {"title": "t"})
    db.add_runtime_span("sp1", RUN, ROOT, "tool", "dup", None, None)  # ignored
    db.finish_runtime_span("sp1", 12.0, "completed")
    row = peek(db, "SELECT kind, status, duration_ms, ended_at FROM runtime_spans WHERE span_id=?",
               "MATCH (n:Span {span_id: $a}) RETURN n.kind AS kind, n.status AS status, "
               "n.duration_ms AS duration_ms, n.ended_at AS ended_at", ("sp1",))
    assert (row["kind"], row["status"], row["duration_ms"]) == ("model", "completed", 12.0)
    assert row["ended_at"]


def test_concurrent_bumps_are_never_lost(db, connect, request):
    """Every bump advances the version by exactly one, however writers overlap: here four
    connections (the gateway process, the host process, parallel workers) at once."""
    if db.__class__.__name__ == "AgentDB":
        request.applymarker(pytest.mark.xfail(
            strict=True, reason="reference bump reads the version, then writes +1, without "
                                "holding its lock or a transaction across the two"))
    writers, bumps = [db] + [connect(db) for _ in range(3)], 25
    threads = [threading.Thread(target=lambda w=w: [w.bump(RUN, ["src/a.py"]) for _ in range(bumps)])
               for w in writers]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert db.current_versions(RUN)["src/a.py"] == str(len(writers) * bumps)


def test_evidence_rows_are_written_once_and_events_in_order(db):
    """The econo-jev addition (contract, Sept 30): evidence keyed by the version read."""
    ref = make_ref("file", "a.py", "sha256:1", "x")
    read = EvidenceEvent("acquired", "read_file", "k", "a.py", ref)
    db.add_evidence_events(RUN, ROOT, 1, [read, EvidenceEvent("mutated", "replace", "w", "a.py")])
    db.add_evidence_events(RUN, ROOT, 3, [read])
    assert db.mutations(RUN) == 1
    if db.__class__.__name__ == "AgentDB":  # the reference exposes no readers; peek with SQL
        assert db.rows("SELECT COUNT(*) FROM evidence WHERE run_id=?", (RUN,))[0][0] == 1
        events = [tuple(r) for r in db.rows("SELECT seq, call_no, event, evidence_id IS NULL "
                                            "FROM evidence_events ORDER BY seq")]
        assert events == [(0, 1, "acquired", 0), (1, 1, "mutated", 1), (2, 3, "acquired", 0)]
        return
    evidence = db.evidence_of(RUN)
    assert len(evidence) == 1 and evidence[0]["source_version"] == "sha256:1"
    assert evidence[0]["recoverable"] is True and evidence[0]["token_size"] == ref.token_size
    events = [(e["seq"], e["call_no"], e["event"], e["evidence_id"] is None)
              for e in db.evidence_events_of(RUN)]
    assert events == [(0, 1, "acquired", False), (1, 1, "mutated", True), (2, 3, "acquired", False)]


def test_one_hour_cache_writes_are_kept_apart(db):
    u = ProviderUsage(uncached_input=10, cache_read=0, cache_write=5, output=1, raw={})
    u.cache_write_1h = 7
    db.add_outcome("o1", RUN, ROOT, None, "agent", u, 1.0, 0.01, True, "promo")
    row = peek(db, "SELECT cache_write, cache_write_1h FROM outcomes WHERE outcome_id=?",
               "MATCH (n:Outcome {outcome_id: $a}) RETURN n.cache_write AS cache_write, "
               "n.cache_write_1h AS cache_write_1h", ("o1",))
    assert (row["cache_write"], row["cache_write_1h"]) == (5, 7)


def test_finishing_a_span_merges_metadata(db):
    db.add_runtime_span("sp1", RUN, ROOT, "dispatch", "task", None, None, {"title": "t", "n": 1})
    db.finish_runtime_span("sp1", 3.0, "completed", {"n": 2, "worker": "w"})
    row = peek(db, "SELECT status, metadata FROM runtime_spans WHERE span_id=?",
               "MATCH (n:Span {span_id: $a}) RETURN n.status AS status, n.metadata AS metadata",
               ("sp1",))
    assert row["status"] == "completed"
    assert J(row["metadata"]) == {"title": "t", "n": 2, "worker": "w"}


# ---------------------------------------------------------------- the contract's own
# operations: those the reference has only as SQL in its callers (Postgres and Neo4j)

@pytest.fixture
def pg(contract_db):
    return contract_db


def test_runs_are_listed_by_label_and_by_having_labels(pg):
    pg.start_run("lbl:baseline:inst-2", "test", "inst-2", "baseline", "measure", "m", 1.0, "fp")
    pg.start_run("other:econo:inst-3", "test", "inst-3", "econo", "observe", "m", 1.0, "fp")
    assert [r["run_id"] for r in pg.list_runs("lbl")] == ["lbl:baseline:inst-2", RUN]
    assert pg.get_run(RUN)["instance_id"] == "inst-1" and pg.get_run("nope") is None
    pg.replace_labels(RUN, [dict(kind="decision", subject_id="d1", agent_id=ROOT, h_actual=3)])
    pg.replace_labels("lbl:baseline:inst-2", [dict(kind="decision", subject_id="d9", h_actual=1)])
    assert pg.labelled_runs() == ["lbl:baseline:inst-2", RUN]
    assert pg.labelled_runs(exclude_instance="inst-1") == ["lbl:baseline:inst-2"]
    assert pg.labelled_runs(exclude_instance="inst-9") == ["lbl:baseline:inst-2", RUN]


def test_labels_are_replaced_whole(pg):
    pg.replace_labels(RUN, [
        dict(kind="decision", subject_id="d1", agent_id=ROOT, h_actual=4),
        dict(kind="result", subject_id="s1", agent_id=ROOT, tool_name="sys_os_read",
             arrived_call=2, needed_calls="[3, 5]", refetched=1, referenced=0),
    ])
    pg.replace_labels(RUN, [
        dict(kind="result", subject_id="s2", agent_id=WORKER, tool_name="grep", arrived_call=1,
             needed_calls=[], refetched=False, referenced=True),
    ])
    rows = pg.result_labels(RUN)
    assert [(r["subject_id"], r["tool_name"], r["needed_calls"], r["refetched"], r["referenced"])
            for r in rows] == [("s2", "grep", [], False, True)]
    pg.replace_labels(RUN, [
        dict(kind="evidence", subject_id="ev1", agent_id=ROOT, tool_name="read_file",
             arrived_call=2, needed_calls=[4], refetched=True, reacquired_same_version=True,
             reacquired_after_mutation=False),
    ])
    ev = pg.labels_of(RUN, "evidence")
    assert len(ev) == 1 and ev[0]["reacquired_same_version"] is True
    assert ev[0]["reacquired_after_mutation"] is False and ev[0]["referenced"] is None
    assert [l["kind"] for l in pg.labels_of(RUN)] == ["evidence"]
    assert len(pg.rows("MATCH (l:Label {run_id: $a}) RETURN l", {"a": RUN})
               if pg.__class__.__name__.startswith("Neo4j")
               else pg.rows("SELECT 1 FROM labels WHERE run_id=%s", (RUN,))) == 1


def test_segment_queries(pg):
    window = conversation()
    for s in window:
        pg.add_segment(s)
        tick()
    pg.add_segment(seg(ROOT, "ai2", SegmentKind.TOOL_CALL, "second call", role="assistant"))
    assert pg.first_segment(ROOT, SegmentKind.TASK).text.startswith("Fix the bug")
    assert pg.first_segment(WORKER, SegmentKind.TASK) is None
    assert [s.native_id for s in pg.segments_of(ROOT, "tool_call")] == ["ai1", "ai2"]
    assert [r["text"] for r in pg.assistant_segments(RUN)] == [window[2].text, "second call"]
    assert all(isinstance(r["created_at"], str) for r in pg.assistant_segments(RUN))


def test_search_segments_ranks_by_terms_matched(pg):
    for s in conversation():
        pg.add_segment(s)
    pg.add_segment(seg(WORKER, "w1", SegmentKind.TOOL_RESULT, "def parse_items(x):\n    return x\n"))
    hits = pg.search_segments(RUN, ["src/app.py"])
    assert hits and all("src/app.py" in h.text for h in hits)
    hits = pg.search_segments(RUN, ["parse_items", "app.py"])
    assert "parse_items" in hits[0].text and "app.py" in hits[0].text  # two terms beat one
    assert len({h.content_hash for h in hits}) == len(hits)              # one per distinct content
    assert pg.search_segments(RUN, []) == [] and pg.search_segments("other-run", ["x"]) == []


def test_tool_result_queries(pg):
    for sid, path, tokens_text in (("ta", "src/a.py", "a" * 40), ("tb", "src/b.py", "b" * 400)):
        s = seg(ROOT, sid, SegmentKind.TOOL_RESULT, tokens_text)
        pg.add_segment(s)
        pg.add_tool_result(sid, RUN, ROOT, "sys_os_read", f"k-{sid}", s.id, {path: "0", "*": "0"},
                           False)
        tick()
    s = seg(ROOT, "tg", SegmentKind.TOOL_RESULT, "grep output")
    pg.add_segment(s)
    pg.add_tool_result("tg", RUN, ROOT, "grep", "k-g", s.id, {"*": "0"}, False)
    rows = pg.tool_results_with_text(RUN)
    assert [r["tool_result_id"] for r in rows] == ["ta", "tb", "tg"]
    assert rows[0]["text"] == "a" * 40 and rows[0]["read_set"] == {"src/a.py": "0", "*": "0"}
    sizes = pg.read_sizes(RUN)
    assert set(sizes) == {"src/a.py", "src/b.py"} and sizes["src/b.py"] > sizes["src/a.py"]
    assert pg.count_tool_results(RUN) == 3


def test_outcome_queries(pg):
    pg.add_outcome("o1", RUN, ROOT, None, "agent", usage(100, 50, 20), 1.5, 0.01, True, "promo")
    tick()
    pg.add_outcome("o2", RUN, WORKER, None, "agent", usage(200, 0, 10), 2.0, 0.02, True, "promo")
    tick()
    pg.add_outcome("o3", RUN, ROOT, None, "compaction",
                   ProviderUsage(None, None, None, None, raw={}), None, None, False, None)
    tot = pg.outcome_totals(RUN)
    assert (tot["calls"], tot["uncached_input"], tot["cache_read"], tot["output"]) == (3, 300, 50, 30)
    assert tot["compaction_calls"] == 1 and tot["incomplete_calls"] == 1
    assert tot["cost_nu"] == pytest.approx(3.5)
    by_agent = {r["agent_id"]: r for r in pg.calls_by_agent(RUN)}
    assert by_agent[ROOT]["calls"] == 2 and by_agent[WORKER]["cost_nu"] == 2.0
    assert [r["agent_id"] for r in pg.worker_calls(RUN)] == [WORKER]
    assert pg.worker_calls(RUN)[0]["calls"] == 1 and isinstance(pg.worker_calls(RUN)[0]["last"], str)
    assert pg.last_prompt_tokens(ROOT) == 0        # the compaction call reported nothing
    assert pg.last_prompt_tokens(WORKER) == 200 and pg.last_prompt_tokens("nobody") is None
    assert [r["outcome_id"] for r in pg.outcomes_of(RUN)] == ["o1", "o2", "o3"]
    assert pg.outcomes_of(RUN)[2]["uncached_input"] is None  # NULL stays NULL
    assert pg.input_tokens_since("2000-01-01T00:00:00+00:00") == 350
    assert pg.input_tokens_since("2999-01-01T00:00:00+00:00") == 0


def test_decision_queries(pg):
    s = seg(ROOT, "c1", SegmentKind.TOOL_RESULT, "tool output", role="tool")
    pg.add_segment(s)
    pg.add_decision(RUN, ROOT, decision("d1", s.id), "observe", 3.0)
    tick()
    pg.add_decision(RUN, ROOT, decision("d2", "args-key", Intercept.BEFORE_TOOL_CALL, "KEEP_FULL"),
                    "observe", 1.0, cache_predicted=10)
    pg.add_decision(RUN, ROOT, decision("d1", s.id), "autopilot", 9.0)  # same id: ignored
    rows = pg.decisions_of(RUN)
    assert [r["decision_id"] for r in rows] == ["d1", "d2"]
    assert rows[0]["predicted_cost"]["leaves_behind"] == 8 and rows[0]["mode"] == "observe"
    assert rows[0]["why_not"] == [{"name": "KEEP_FULL", "why_not": "costlier"}]
    summary = pg.decision_summary(RUN)
    assert [(r["intercept"], r["chosen"], r["n"]) for r in summary["by_choice"]] == [
        ("admit_tool_result", "POINTER", 1), ("before_tool_call", "KEEP_FULL", 1)]
    assert summary["feasible_counts"] == {"KEEP_FULL": 2, "POINTER": 2}

    pg.add_outcome("o1", RUN, ROOT, "d1", "agent", usage(), 7.0, 0.07, True, "promo")
    pairs = pg.predicted_vs_actual(RUN)
    assert len(pairs) == 1 and pairs[0]["predicted_cost"]["prepare"] == 2 and pairs[0]["cost_nu"] == 7.0

    pg.replace_labels(RUN, [
        dict(kind="decision", subject_id="d1", agent_id=ROOT, h_actual=4),
        dict(kind="result", subject_id=s.id, agent_id=ROOT, tool_name="sys_os_read",
             arrived_call=1, needed_calls=[2], refetched=True, referenced=False),
    ])
    replay = pg.replay_rows(RUN)
    assert len(replay) == 1 and replay[0]["h_actual"] == 4 and replay[0]["needed_calls"] == [2]
    assert replay[0]["decision_id"] == "d1" and replay[0]["tool_name"] == "sys_os_read"


def test_span_queries(pg):
    pg.add_runtime_span("m1", RUN, ROOT, "model", "chat", None, None)
    tick()
    pg.add_runtime_span("t1", RUN, ROOT, "tool", "grep", "call-1", None, {"args": 1})
    tick()
    pg.add_runtime_span("m2", RUN, WORKER, "model", "chat", None, None)
    assert pg.has_open_model_span(ROOT) and pg.count_model_spans(RUN) == 2
    pg.finish_runtime_span("m1", 5.0, "completed")
    assert not pg.has_open_model_span(ROOT) and pg.has_open_model_span(WORKER)
    assert [r["span_id"] for r in pg.spans_of(RUN)] == ["m1", "t1", "m2"]
    assert [r["span_id"] for r in pg.spans_of(RUN, "tool")] == ["t1"]
    assert pg.spans_of(RUN, "tool")[0]["metadata"] == {"args": 1}


def test_gateway_pointers_upsert(pg):
    pg.add_gateway_pointer(RUN, ROOT, "call-1", "pointer v1")
    pg.add_gateway_pointer(RUN, ROOT, "call-1", "pointer v2")
    pg.add_gateway_pointer(RUN, WORKER, "call-1", "worker's")
    assert pg.gateway_pointers(RUN, ROOT) == {"call-1": "pointer v2"}
    assert pg.gateway_pointers(RUN, "nobody") == {}


def test_write_once_tables_ignore_repeats(pg):
    s = seg(ROOT, "c1", SegmentKind.TOOL_RESULT, "x")
    pg.add_segment(s)
    pg.add_tool_result("t1", RUN, ROOT, "read", "k", s.id, {"/a": "0"}, False)
    pg.add_tool_result("t1", RUN, ROOT, "read", "other", s.id, {"/b": "0"}, True)
    assert pg.find_tool_result(RUN, "read", "k") is not None
    pg.add_stored_result("r1", RUN, "task", WORKER, "first", {"/a": "0"}, False)
    pg.add_stored_result("r1", RUN, "task", WORKER, "second", {"/a": "0"}, False)
    assert pg.find_stored_result(RUN, "task")["result_text"] == "first"
