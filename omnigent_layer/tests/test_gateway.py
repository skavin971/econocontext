"""The gateway against a fake upstream: pass-through, measurement, arms, caps, the key."""

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import omnigent_layer
from econocontext.store.db import AgentDB
from omnigent_layer import gateway, register_run

REPLY = {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
         "usage": {"prompt_tokens": 100, "completion_tokens": 2, "total_tokens": 102}}


class Upstream(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Upstream.seen.append((self.path, dict(self.headers), body))
        data = json.dumps(REPLY).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def gw(monkeypatch):
    Upstream.seen = []
    upstream = serve(Upstream)
    monkeypatch.setattr(gateway, "UPSTREAM", f"http://127.0.0.1:{upstream.server_port}")
    monkeypatch.setattr(gateway, "KEY", "real-key")
    gateway.Gateway.db = AgentDB(omnigent_layer.DB_PATH)
    server = serve(gateway.Gateway)
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    upstream.shutdown()


def post(url, body):
    request = urllib.request.Request(url, json.dumps(body).encode(), {
        "Content-Type": "application/json", "Authorization": "Bearer placeholder"})
    return json.load(urllib.request.urlopen(request, timeout=30))


BODY = {"model": "m", "messages": [{"role": "system", "content": "s"},
                                   {"role": "user", "content": "task"}]}


def test_baseline_is_forwarded_byte_for_byte_and_measured(gw):
    register_run("b1", "baseline", "observe")
    assert post(f"{gw}/run/b1/v1/chat/completions", BODY) == REPLY
    path, headers, body = Upstream.seen[0]
    assert path == "/chat/completions" and json.loads(body) == BODY
    db = AgentDB(omnigent_layer.DB_PATH)
    assert db.rows("SELECT uncached_input FROM outcomes WHERE run_id='b1'")[0][0] == 100
    assert not db.rows("SELECT * FROM decisions WHERE run_id='b1'")  # baseline: no decisions


def test_the_real_key_replaces_the_placeholder(gw):
    register_run("k1", "baseline", "observe")
    post(f"{gw}/run/k1/v1/chat/completions", BODY)
    headers = {k.lower(): v for k, v in Upstream.seen[0][1].items()}
    assert headers["x-goog-api-key"] == "real-key" and "authorization" not in headers


def test_econo_arm_logs_a_plan_prompt_decision_per_call(gw):
    register_run("e1", "econo", "observe")
    post(f"{gw}/run/e1/agent/helper/v1/chat/completions", BODY)
    rows = AgentDB(omnigent_layer.DB_PATH).rows(
        "SELECT agent_id, intercept FROM decisions WHERE run_id='e1'")
    assert [tuple(r) for r in rows] == [("e1:helper", "plan_prompt")]


def test_unregistered_runs_and_other_paths_are_refused(gw):
    with pytest.raises(urllib.error.HTTPError) as err:
        post(f"{gw}/run/nope/v1/chat/completions", BODY)
    assert err.value.code == 400
    register_run("p1", "baseline", "observe")
    with pytest.raises(urllib.error.HTTPError) as err:
        post(f"{gw}/run/p1/v1/responses", BODY)
    assert err.value.code == 404 and not Upstream.seen


def test_call_cap_stops_a_run(gw, monkeypatch):
    monkeypatch.setattr(gateway, "MAX_CALLS_PER_RUN", 1)
    register_run("c1", "baseline", "observe")
    post(f"{gw}/run/c1/v1/chat/completions", BODY)
    with pytest.raises(urllib.error.HTTPError) as err:
        post(f"{gw}/run/c1/v1/chat/completions", BODY)
    assert err.value.code == 429 and len(Upstream.seen) == 1
