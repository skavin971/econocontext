"""The gateway against a fake upstream: pass-through, measurement, arms, caps, the key."""

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import omnigent_layer
from econocontext.store.db import AgentDB
from omnigent_layer import gateway, gateway_common, register_run

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
    monkeypatch.setattr(gateway_common, "KEY", "real-key")
    gateway.Gateway.db = AgentDB(omnigent_layer.DB_PATH)
    gateway.Gateway.db.execute(gateway.POINTERS_TABLE)
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


def test_each_call_is_priced_and_timed(gw):
    register_run("t1", "econo", "observe")
    post(f"{gw}/run/t1/v1/chat/completions", BODY)
    db = AgentDB(omnigent_layer.DB_PATH)
    outcome = db.rows("SELECT outcome_id, cost_usd, cost_complete FROM outcomes WHERE run_id='t1'")[0]
    assert outcome["cost_complete"] == 1 and outcome["cost_usd"] > 0
    span = db.rows("SELECT kind, status, native_id FROM runtime_spans WHERE run_id='t1'")[0]
    assert (span["kind"], span["status"], span["native_id"]) == ("model", "completed",
                                                                 outcome["outcome_id"])


def test_the_real_key_replaces_the_placeholder(gw):
    register_run("k1", "baseline", "observe")
    post(f"{gw}/run/k1/v1/chat/completions", BODY)
    headers = {k.lower(): v for k, v in Upstream.seen[0][1].items()}
    assert headers["x-goog-api-key"] == "real-key" and "authorization" not in headers


def test_econo_arm_logs_a_plan_prompt_decision_per_call(gw):
    register_run("e1", "econo", "observe")
    post(f"{gw}/run/e1/v1/chat/completions", BODY)
    rows = AgentDB(omnigent_layer.DB_PATH).rows(
        "SELECT agent_id, intercept FROM decisions WHERE run_id='e1'")
    assert [tuple(r) for r in rows] == [("e1:root", "plan_prompt")]


def test_a_jev_run_id_is_routed(gw):
    run = "j1:econo+jev:pytest-dev__pytest-5809"  # bench/run.py's id for a --jev run
    register_run(run, "econo", "observe")
    assert post(f"{gw}/run/{run}/v1/chat/completions", BODY) == REPLY


def test_current_route_and_two_workers_stay_apart(gw):
    register_run("w1", "econo", "observe", current=True)
    for task in ("find where lexer is set", "run the pastebin tests"):
        post(f"{gw}/current/agent/worker/v1/chat/completions",
             {"model": "m", "messages": [{"role": "system", "content": "s"},
                                         {"role": "user", "content": task}]})
    agents = {r[0] for r in AgentDB(omnigent_layer.DB_PATH).rows(
        "SELECT agent_id FROM outcomes WHERE run_id='w1'")}
    assert len(agents) == 2 and all(a.startswith("w1:worker:") for a in agents)


def test_unregistered_runs_and_other_paths_are_refused(gw):
    with pytest.raises(urllib.error.HTTPError) as err:
        post(f"{gw}/run/nope/v1/chat/completions", BODY)
    assert err.value.code == 400
    register_run("p1", "baseline", "observe")
    with pytest.raises(urllib.error.HTTPError) as err:
        post(f"{gw}/run/p1/v1/responses", BODY)
    assert err.value.code == 404 and not Upstream.seen


def test_call_cap_stops_a_run(gw, monkeypatch):
    monkeypatch.setattr(gateway_common, "MAX_CALLS_PER_RUN", 1)
    register_run("c1", "baseline", "observe")
    post(f"{gw}/run/c1/v1/chat/completions", BODY)
    with pytest.raises(urllib.error.HTTPError) as err:
        post(f"{gw}/run/c1/v1/chat/completions", BODY)
    assert err.value.code == 429 and len(Upstream.seen) == 1


def test_a_run_can_have_a_lower_call_cap(gw):
    register_run("c2", "baseline", "observe", overrides={"limits": {"max_model_calls": 1}})
    post(f"{gw}/run/c2/v1/chat/completions", BODY)
    with pytest.raises(urllib.error.HTTPError) as err:
        post(f"{gw}/run/c2/v1/chat/completions", BODY)
    assert err.value.code == 429 and len(Upstream.seen) == 1


def test_autopilot_points_out_an_old_result_and_keeps_it_pointed_out(gw, tmp_path):
    register_run("p1", "econo", "autopilot", workdir=str(tmp_path), overrides={
        "allowlist": {"COMMIT_PENDING": True}, "constraints": {"max_quality_risk": 0.2}})
    big = "\n".join(f"line {i}: " + "x" * 80 for i in range(150))
    call = {"id": "t1", "type": "function", "function": {"name": "sys_os_read", "arguments": "{}"}}
    history = [{"role": "system", "content": "s"}, {"role": "user", "content": "fix it"},
               {"role": "assistant", "content": None, "tool_calls": [call]},
               {"role": "tool", "tool_call_id": "t1", "content": big},
               {"role": "assistant", "content": "looked at it"}, {"role": "user", "content": "go on"}]
    for turn in range(2):
        post(f"{gw}/run/p1/v1/chat/completions", {"model": "m", "messages": history})
        sent = json.loads(Upstream.seen[-1][2])["messages"][3]["content"]
        assert "Full output:" in sent and big not in sent, f"turn {turn}"
    [pointer] = list((tmp_path / ".econocontext" / "pointers").iterdir())
    assert pointer.read_text() == big
