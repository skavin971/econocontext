import json
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import omnigent_layer
from omnigent_layer import gateway, research, register_run
from omnigent_layer.policy import econocontext
from econocontext.observation import observe
from econocontext.research.__main__ import connect, summary
from econocontext.research.__main__ import unpack
from econocontext.store.db import AgentDB

BODY = {"model": "google/gemini-3.6-flash", "messages": [{"role": "system", "content": "system"},
        {"role": "user", "content": "task"}]}
USAGE = {"prompt_tokens": 100, "completion_tokens": 2, "total_tokens": 102}
REPLY = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "final answer"}}],
         "usage": USAGE}


@pytest.fixture
def recorded_gateway(monkeypatch, tmp_path):
    state = {"stream": False, "http_status": 200, "seen": [], "done": True}
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            state["seen"].append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(state["http_status"])
            self.send_header("Connection", "close")
            self.end_headers()
            if state["stream"]:
                for content in ("final ", "answer"):
                    chunk = {"choices": [{"index": 0, "delta": {"content": content}}]}
                    self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
                self.wfile.write(b"data: " + json.dumps({"choices": [], "usage": USAGE}).encode() + b"\n\n")
                if state["done"]:
                    self.wfile.write(b"data: [DONE]\n\n")
            else:
                self.wfile.write(json.dumps(REPLY).encode())
            self.close_connection = True

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    monkeypatch.setattr(gateway, "UPSTREAM", f"http://127.0.0.1:{upstream.server_port}")
    monkeypatch.setattr(gateway, "KEY", "secret-test-key")
    gateway.Gateway.db = AgentDB(omnigent_layer.DB_PATH)
    gateway.Gateway.db.execute(gateway.POINTERS_TABLE)
    server = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Gateway)
    for s in (upstream, server):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    def send(run, body=BODY):
        raw = json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/run/{run}/v1/chat/completions", raw)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
    yield state, send, tmp_path / "research.db"
    # server_close waits for request threads, including their trailing accounting.
    for s in (server, upstream):
        s.shutdown()
        s.server_close()


def wait_finished(sink, count=1):
    # Responses can reach the client before the gateway finishes trailing accounting.
    import time
    for _ in range(100):
        with sink.lock:
            done = sink.conn.execute("SELECT COUNT(*) FROM model_calls WHERE run_id=? AND status!='open'",
                                     (sink.run_id,)).fetchone()[0]
        if done >= count or sink.failed:
            break
        time.sleep(0.01)
    assert done == count and not sink.failed


@pytest.mark.parametrize("arm", ["baseline", "econo"])
def test_full_requests_final_answer_and_cost_in_both_arms(recorded_gateway, arm):
    state, send, path = recorded_gateway
    sink = research.start("r", {"arm": arm})
    register_run("r", arm, "observe")
    status, reply = send("r")
    wait_finished(sink)
    assert status == 200 and json.loads(reply) == REPLY
    call = sink.conn.execute("SELECT call_id,received_blob,sent_blob,response_blob FROM model_calls").fetchone()
    assert call[1] == call[2]  # observe and baseline send the original JSON payload
    assert sink.conn.execute("SELECT COUNT(*) FROM call_messages").fetchone()[0] == 5
    cost = sink.conn.execute("SELECT cost_usd,cost_complete FROM usage_costs").fetchone()
    assert cost[0] > 0 and cost[1] == 1
    assert sink.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == (arm == "econo")
    # Authentication headers never enter the research DB.
    assert not any(b"secret-test-key" in bytes(r[0]) for r in sink.conn.execute("SELECT data FROM blobs"))
    assert not any("secret-test-key" in r[0] for r in sink.conn.execute("SELECT payload FROM events"))


@pytest.mark.parametrize("done", [True, False])
def test_streaming_reply_and_partial_capture(recorded_gateway, done):
    state, send, path = recorded_gateway
    state.update(stream=True, done=done)
    sink = research.start("r", {})
    register_run("r", "baseline", "observe")
    status, data = send("r", {**BODY, "stream": True})
    wait_finished(sink)
    assert status == 200 and (b"[DONE]" in data) == done
    response = sink.conn.execute("SELECT payload FROM call_messages WHERE view='response'").fetchone()[0]
    assert json.loads(response)["content"] == "final answer"
    assert sink.conn.execute("SELECT response_complete FROM model_calls").fetchone()[0] == done
    assert sink.conn.execute("SELECT COUNT(*) FROM stream_chunks").fetchone()[0] >= 6
    observe(sink, "run.finished", {"status": "done"})
    assert summary(connect(path), sink.run_id, path)["capture_status"] == ("complete" if done else "partial")


def test_refusal_and_upstream_error_are_attempts(recorded_gateway, monkeypatch):
    state, send, path = recorded_gateway
    sink = research.start("r", {})
    register_run("r", "baseline", "observe")
    state["http_status"] = 503
    assert send("r")[0] == 503
    wait_finished(sink)
    monkeypatch.setattr(gateway, "MAX_CALLS_PER_RUN", 0)
    assert send("r")[0] == 429
    wait_finished(sink, 2)
    assert [r[0] for r in sink.conn.execute("SELECT status FROM model_calls ORDER BY started_at")] == ["http_error", "refused"]


def test_failed_or_missing_recorder_does_not_change_wire_behavior(recorded_gateway, monkeypatch):
    state, send, path = recorded_gateway
    register_run("off", "econo", "observe")
    without = send("off")
    assert not path.exists()
    sink = research.start("on", {})
    register_run("on", "econo", "observe")
    with_logging = send("on")
    wait_finished(sink)
    def broken(*args):
        raise sqlite3.OperationalError("database locked")
    monkeypatch.setattr(sink, "_project", broken)
    with_failure = send("on")
    assert sink.failed
    assert without == with_logging == with_failure
    assert len(set(state["seen"])) == 1


def test_tool_events_structured_results_and_ambiguous_ids(tmp_path):
    sink = research.start("r", {})
    register_run("r", "baseline", "observe")
    policy = econocontext("r")
    event = {"type": "tool_result", "target": "read", "request_data": {"arguments": {"path": "x"}},
             "data": {"result": {"content": [{"type": "text", "text": "structured output"}]}}}
    assert policy(event) is None
    assert policy(event) is None  # identical events are occurrences, not duplicate content
    assert not sink.failed
    assert sink.conn.execute("SELECT COUNT(*) FROM tool_events").fetchone()[0] == 2
    assert sink.conn.execute("SELECT attribution FROM tool_events").fetchone()[0] == "unresolved"
    assert AgentDB(omnigent_layer.DB_PATH).rows("SELECT * FROM decisions") == []


def test_repeated_runtime_id_gets_distinct_trace_even_with_cached_engine(recorded_gateway):
    state, send, path = recorded_gateway
    first = research.start("r", {})
    register_run("r", "econo", "observe")
    send("r")
    wait_finished(first)
    research.stop("r", first)
    second = research.start("r", {})
    send("r")
    wait_finished(second)
    assert first.run_id != second.run_id
    assert second.conn.execute("SELECT COUNT(*) FROM decisions WHERE run_id=?", (second.run_id,)).fetchone()[0] == 1


@pytest.mark.parametrize("timeout", [False, True])
def test_shell_captures_output_before_truncation_and_timeout(tmp_path, monkeypatch, timeout):
    import subprocess
    from omnigent_layer import tools
    monkeypatch.chdir(tmp_path)
    sink = research.start("r", {}, str(tmp_path))
    output = "é" * 40000
    monkeypatch.setattr(tools, "_container_for", lambda _: ("fake", {"econocontext.mount": "/testbed"}))
    def run(*args, **kwargs):
        if timeout:
            raise subprocess.TimeoutExpired("fake", 300, output=output.encode(), stderr=b"partial error")
        return subprocess.CompletedProcess("fake", 7, output, "error")
    monkeypatch.setattr(tools.subprocess, "run", run)
    visible = tools.container_shell("command")
    if timeout:
        assert "timed out" in visible
    else:
        assert "[output truncated]" in visible and len(visible) < len(output)
    conn = connect(sink.path)
    event = conn.execute("SELECT payload FROM events WHERE kind='shell.output'").fetchone()
    payload = unpack(conn, json.loads(event[0]))
    if timeout:
        import base64
        assert base64.b64decode(payload["stdout"]["data"]).decode() == output
    else:
        assert payload["stdout"] == output and payload["exit_code"] == 7
    assert payload["timed_out"] == timeout


def test_new_run_replaces_stale_discovery_and_stop_cleans_up(tmp_path):
    first = research.start("r", {}, str(tmp_path))
    second = research.start("r", {}, str(tmp_path))
    research.stop("r", first, str(tmp_path))
    assert research.recorder_for("r") is second
    assert research.recorder_for_workspace(str(tmp_path)) is second
    research.stop("r", second, str(tmp_path))
    assert research.recorder_for("r") is None
    assert research.recorder_for_workspace(str(tmp_path)) is None


def test_original_and_transformed_requests_and_decision_link(recorded_gateway, monkeypatch):
    state, send, path = recorded_gateway
    sink = research.start("r", {})
    register_run("r", "econo", "autopilot")
    original = gateway.Gateway.plan
    def plan(self, engine, run, agent, body):
        # Exercise the transport boundary independently of optimizer choices.
        body, decision_id = original(self, engine, run, agent, body)
        return {**body, "messages": [{"role": "user", "content": "transformed"}]}, decision_id
    monkeypatch.setattr(gateway.Gateway, "plan", plan)
    assert send("r")[0] == 200
    wait_finished(sink)
    call = sink.conn.execute("SELECT received_blob,sent_blob,decision_id,call_id FROM model_calls").fetchone()
    assert call[0] != call[1] and json.loads(state["seen"][0])["messages"][0]["content"] == "transformed"
    decision = sink.conn.execute("SELECT decision_id,call_id FROM decisions").fetchone()
    assert tuple(decision) == tuple(call[2:])


@pytest.mark.parametrize("arm", ["baseline", "econo"])
def test_benchmark_command_records_automatically(recorded_gateway, monkeypatch, arm):
    import importlib.util
    import sys
    from pathlib import Path
    from types import ModuleType, SimpleNamespace

    # Stub the external runner imports; the real CLI, run lifecycle, recorder,
    # policy, and gateway execute without requiring Omnigent or paid model calls.
    imports = {
        "omnigent": [], "omnigent.host": [],
        "omnigent.chat": ["_prepare_chat_session_via_daemon", "_remote_headers",
                          "_server_auth", "_stop_headless_session"],
        "omnigent.cli": ["_bundle"],
        "omnigent.host.identity": ["load_or_create_host_identity"],
        "omnigent_client": ["OmnigentClient", "SessionsChat"],
    }
    for name, attributes in imports.items():
        module = ModuleType(name)
        for attribute in attributes:
            setattr(module, attribute, None)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(sys, "path", list(sys.path))
    spec = importlib.util.spec_from_file_location(
        "research_bench_test", Path(__file__).resolve().parents[2] / "bench" / "run.py")
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)

    state, send, path = recorded_gateway
    monkeypatch.setattr(bench, "HOME", path.parent)
    monkeypatch.setattr(bench, "load", lambda _: [SimpleNamespace(instance_id="task")])
    monkeypatch.setattr(bench, "snapshot", lambda *args: {"arm": arm})
    seen = {}
    def execute(args, instance, run_id, safe, workdir, container, agent_spec, overrides, sink):
        assert not hasattr(args, "research_db")
        assert "omnigent_layer.policy.econocontext" in agent_spec.read_text()
        assert research.recorder_for_workspace(workdir) is sink
        seen.update(trace=sink.run_id, runtime=run_id, workspace=workdir)
        register_run(run_id, arm, "observe")
        assert send(run_id)[0] == 200
        wait_finished(sink)
        econocontext(run_id)({"type": "tool_result", "target": "read",
                             "data": {"result": "file contents"}})
        observe(sink, "run.finished", {"status": "done"})
    monkeypatch.setattr(bench, "execute_run", execute)
    monkeypatch.setattr(sys, "argv", ["bench/run.py", "--label", "test",
                                     "--instance", "task", "--arm", arm])
    bench.main()
    with connect(path) as conn:
        assert conn.execute("SELECT status FROM runs").fetchone()[0] == "done"
        assert conn.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM tool_events").fetchone()[0] == 1
    assert research.recorder_for(seen["runtime"]) is None
    assert research.recorder_for_workspace(seen["workspace"]) is None
