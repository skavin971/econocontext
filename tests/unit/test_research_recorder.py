import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import pytest

from econocontext.observation import observe
from econocontext.research import ResearchRecorder
from econocontext.research.__main__ import connect, export, summary, unpack
from econocontext.research.capture import CallCapture
from econocontext.research.stream import StreamResponse
from econocontext.store.db import AgentDB


def rows(sink, sql):
    sink.conn.row_factory = sqlite3.Row
    return [dict(r) for r in sink.conn.execute(sql)]


def test_separate_database_rejects_operational_file_and_aliases(tmp_path):
    operational = tmp_path / "memory.db"
    db = AgentDB(operational)
    db.conn.close()
    before = operational.read_bytes()
    with pytest.raises(ValueError):
        ResearchRecorder.start(operational, "r", {})
    with pytest.raises(ValueError):
        ResearchRecorder.start(operational, "r", {}, operational_path=operational)
    alias = tmp_path / "alias.db"
    alias.symlink_to(operational)
    with pytest.raises(ValueError):
        ResearchRecorder.start(alias, "r", {}, operational_path=operational)
    assert operational.read_bytes() == before


def test_unique_trials_and_exact_payloads_survive_without_operational_db(tmp_path, cfg):
    path = tmp_path / "research.db"
    sink = ResearchRecorder.start(path, "same-runtime-id", {"pricing": asdict(cfg.card)})
    other = ResearchRecorder.start(path, "same-runtime-id", {})
    assert sink.run_id != other.run_id
    body = {"model": cfg.card.model, "messages": [{"role": "developer", "content": "é" * 9000},
            {"role": "user", "content": [{"type": "text", "text": "hello"}]}]}
    raw = json.dumps(body, indent=3).encode()
    capture = CallCapture(sink, "call", raw, body, agent_id="root")
    capture.sent(raw, body)
    reply = {"choices": [{"message": {"role": "assistant", "content": "FINAL"}}]}
    capture.response(json.dumps(reply).encode())
    capture.finish("completed", 200, raw_usage={})
    observe(sink, "run.finished", {"status": "done"})
    assert not sink.failed
    conn = connect(path)
    result = summary(conn, sink.run_id, path)
    assert result["capture_status"] == "complete"
    assert result["cost_complete"] is False  # absent usage is not free
    messages = rows(sink, "SELECT * FROM call_messages")
    assert unpack(conn, json.loads(messages[-1]["payload"]))["content"] == "FINAL"
    received = rows(sink, "SELECT received_blob FROM model_calls")[0]["received_blob"]
    assert bytes(conn.execute("SELECT data FROM blobs WHERE blob_key=?", (received,)).fetchone()[0]) == raw
    assert "FINAL" in "".join(export(conn, sink.run_id))


def test_idempotent_events_and_concurrent_process_connections(tmp_path):
    path = tmp_path / "research.db"
    root = ResearchRecorder.start(path, "r", {})
    sinks = [ResearchRecorder(path, root.run_id) for _ in range(4)]
    def write(i):
        for n in range(8):
            sinks[i].emit("example", {"n": n}, event_id=f"{i}-{n}")
            sinks[i].emit("example", {"n": n}, event_id=f"{i}-{n}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(4)))
    assert not any(s.failed for s in sinks)
    events = rows(root, "SELECT * FROM events")
    assert len(events) == 33
    assert len({e["sequence"] for e in events}) == 33


def test_logging_failure_is_visible_and_does_not_raise(tmp_path, monkeypatch):
    path = tmp_path / "research.db"
    sink = ResearchRecorder.start(path, "r", {})
    def broken(*args):
        raise sqlite3.OperationalError("disk full")
    monkeypatch.setattr(sink, "_project", broken)
    sink.emit("example", {"value": 1})
    assert sink.failed
    report = summary(connect(path), sink.run_id, path)
    assert report["capture_status"] == "partial"
    assert report["capture_failures"][0]["error"] == "OperationalError"
    assert len(rows(sink, "SELECT * FROM events")) == 1  # failed transaction rolled back


def test_partial_stream_preserved_and_arguments_reassembled(tmp_path):
    path = tmp_path / "research.db"
    sink = ResearchRecorder.start(path, "r", {})
    body = {"model": "fake", "messages": []}
    capture = CallCapture(sink, "c", json.dumps(body).encode(), body)
    for i, fragment in enumerate(['{"path":', '"é.py"}']):
        tool = {"index": 0, "function": {"arguments": fragment}}
        if i == 0:
            tool.update(id="t1", type="function")
            tool["function"]["name"] = "read"
        chunk = {"choices": [{"index": 0, "delta": {"tool_calls": [tool]}}]}
        capture.chunk(b"data: " + json.dumps(chunk).encode() + b"\n")
        capture.chunk(b"\n")
    capture.finish("error", 200, error={"type": "disconnect"})
    observe(sink, "run.finished", {"status": "error"})
    assert not sink.failed
    assert len(rows(sink, "SELECT * FROM stream_chunks")) == 4
    m = json.loads(rows(sink, "SELECT payload FROM call_messages WHERE view='response'")[0]["payload"])
    assert json.loads(m["tool_calls"][0]["function"]["arguments"]) == {"path": "é.py"}
    assert summary(connect(path), sink.run_id, path)["capture_status"] == "partial"


def test_complete_sse_and_multiple_choices():
    stream = StreamResponse()
    for part in [b'data: {"choices":[{"index":0,"delta":{"content":"a"}},{"index":1,"delta":{"content":"b"}}]}\n',
                 b'\n', b'data: [DONE]\n', b'\n']:
        stream.feed(part)
    assert stream.done and not stream.invalid
    assert [c["message"]["content"] for c in stream.body()["choices"]] == ["a", "b"]


def test_decisions_copied_without_altering_optimizer(engine):
    from econocontext.types import HostRequest
    from tests.unit.conftest import conversation
    eco = engine()
    path = eco.db.conn.execute("PRAGMA database_list").fetchone()[2] + ".research"
    recorder = ResearchRecorder.start(path, "r", {})
    eco.observer = recorder
    request = HostRequest("run-1:root", conversation())
    before = eco.plan_prompt("run-1:root", request)
    assert not recorder.failed
    decision = rows(recorder, "SELECT * FROM decisions")[0]
    assert decision["decision_id"] == before.decision_id
    class BrokenSink:
        def emit(self, *args, **kwargs):
            raise RuntimeError("logging offline")
    eco.observer = BrokenSink()
    after = eco.plan_prompt("run-1:root", request)
    assert [s.text for s in before.segments] == [s.text for s in after.segments]
    assert before.applied == after.applied


def test_reserved_json_keys_and_offline_export_round_trip(tmp_path, monkeypatch, capsys):
    import sys
    from econocontext.research.__main__ import main
    path = tmp_path / "research.db"
    sink = ResearchRecorder.start(path, "r", {})
    payload = {"$blob": "user-supplied", "size": 42, "nested": {"$literal": {"text": "é" * 5000}}}
    observe(sink, "example", payload)
    observe(sink, "run.finished", {"status": "done"})
    sink.close()
    before = path.read_bytes()
    monkeypatch.setattr(sys, "argv", ["research", "--db", str(path), "export", "--run", sink.run_id])
    main()
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[1]["payload"] == payload
    assert path.read_bytes() == before


def test_future_schema_refused_without_modification(tmp_path):
    path = tmp_path / "research.db"
    sink = ResearchRecorder.start(path, "r", {})
    sink.conn.execute("PRAGMA user_version=999")
    sink.close()
    before = path.read_bytes()
    with pytest.raises(ValueError, match="newer"):
        ResearchRecorder(path, "r")
    assert path.read_bytes() == before


def test_auxiliary_predictor_preserves_raw_reply_and_unknown_cost(engine, tmp_path, monkeypatch):
    from io import BytesIO
    from econocontext.planner import jev_planner
    from econocontext.types import HostRequest, ToolResultEvent
    from tests.unit.conftest import conversation
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    response = {"answers": {"needed_again": {"type": "noul", "noul": 0.82}}, "usage": {"tokens": 123}}
    monkeypatch.setattr(jev_planner, "urlopen", lambda *a, **kw: BytesIO(json.dumps(response).encode()))
    sink = ResearchRecorder.start(tmp_path / "research.db", "r", {})
    eco = engine(jev=True, observer=sink)
    eco.plan_prompt("run-1:root", HostRequest("run-1:root", conversation()))
    admitted = eco.admit_tool_result("run-1:root", ToolResultEvent(
        "run-1:root", "c9", "read_file", "k9", "large result\n" * 2000,
        "/testbed/big.py", ["/testbed/big.py"], False))
    decision = json.loads(rows(sink, f"SELECT payload FROM decisions WHERE decision_id='{admitted.decision_id}'")[0]["payload"])
    assert decision["decision"]["prediction"]["p_need_again"] == 0.82
    call = rows(sink, "SELECT * FROM model_calls")[0]
    assert call["purpose"] == "predictor" and call["status"] == "completed"
    raw = sink.conn.execute("SELECT data FROM blobs WHERE blob_key=?", (call["response_blob"],)).fetchone()[0]
    assert json.loads(raw) == response
    usage = rows(sink, "SELECT * FROM usage_costs")[0]
    assert usage["cost_usd"] is None and usage["cost_complete"] == 0
    assert eco.db.rows("SELECT * FROM outcomes") == []
