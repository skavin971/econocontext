"""The Gemini route against a fake upstream: pass-through, streaming, usage, the key, caps."""

import json
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

import pytest

import omnigent_layer
from econocontext.store.db import AgentDB
from omnigent_layer import gateway, gateway_common, register_run
from test_gateway import serve

MODEL = "/v1beta1/publishers/google/models/gemini-3.6-flash"
USAGE = {"promptTokenCount": 1000, "cachedContentTokenCount": 600, "candidatesTokenCount": 20,
         "thoughtsTokenCount": 80, "totalTokenCount": 1100}
REPLY = {"candidates": [{"content": {"role": "model", "parts": [{"text": "hi"}]}}],
         "usageMetadata": USAGE}
CHUNKS = [{"candidates": [{"content": {"role": "model", "parts": [{"text": "h"}]}}],
           "usageMetadata": {"trafficType": "ON_DEMAND"}},
          {"candidates": [{"content": {"role": "model", "parts": [{"text": "i"}]},
                           "finishReason": "STOP"}], "usageMetadata": USAGE}]
BODY = {"contents": [{"role": "user", "parts": [{"text": "task"}]}],
        "systemInstruction": {"parts": [{"text": "s"}]}}


class Upstream(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *args):
        pass

    def reply(self, status, data: bytes, kind="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Upstream.seen.append((self.path, dict(self.headers), body))
        if "bad-model" in self.path:
            return self.reply(429, b'{"error": {"code": 429, "message": "quota"}}')
        if ":streamGenerateContent" in self.path:
            sse = b"".join(b"data: " + json.dumps(c).encode() + b"\r\n\r\n" for c in CHUNKS)
            return self.reply(200, sse, "text/event-stream")
        if ":countTokens" in self.path:
            return self.reply(200, b'{"totalTokens": 7}')
        if ":generateContent" in self.path:
            return self.reply(200, json.dumps(REPLY).encode())
        return self.reply(200, b"not json")


@pytest.fixture
def gw(monkeypatch):
    Upstream.seen = []
    upstream = serve(Upstream)
    monkeypatch.setattr(gateway, "GEMINI_UPSTREAM", f"http://127.0.0.1:{upstream.server_port}")
    monkeypatch.setattr(gateway_common, "KEY", "real-key")
    monkeypatch.delenv("ECONOCONTEXT_GEMINI_UPSTREAM_KEY", raising=False)
    gateway.Gateway.db = AgentDB(omnigent_layer.DB_PATH)
    server = serve(gateway.Gateway)
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    upstream.shutdown()


def post(url, body, headers=None) -> tuple[int, bytes]:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    request = urllib.request.Request(url, raw, {"Content-Type": "application/json",
                                                "x-goog-api-key": "econo-placeholder",
                                                **(headers or {})})
    try:
        reply = urllib.request.urlopen(request, timeout=30)
        return reply.status, reply.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def db():
    return AgentDB(omnigent_layer.DB_PATH)


def eventually(read, seconds=3.0):
    """The gateway logs after the reply has gone out: wait for it briefly."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            return read()
        except FileNotFoundError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.02)


def test_a_model_call_is_forwarded_byte_for_byte_and_measured(gw):
    register_run("g1", "baseline", "observe", host="omnigent:gemini")
    raw = b'{"contents": [{"role": "user", "parts": [{"text": "task"}]}],   "odd": 1}'
    status, reply = post(f"{gw}/run/g1/gemini{MODEL}:generateContent", raw)
    assert status == 200 and json.loads(reply) == REPLY
    path, _, body = Upstream.seen[0]
    assert path == f"{MODEL}:generateContent" and body == raw  # the same bytes
    outcome = db().rows("SELECT * FROM outcomes WHERE run_id='g1'")[0]
    assert (outcome["uncached_input"], outcome["cache_read"], outcome["output"],
            outcome["reasoning"]) == (400, 600, 100, 80)
    assert outcome["cost_complete"] == 1 and outcome["cost_usd"] > 0
    span = db().rows("SELECT kind, name, status FROM runtime_spans WHERE run_id='g1'")[0]
    assert tuple(span) == ("model", "gemini-3.6-flash", "completed")


def test_a_stream_is_relayed_and_its_last_usage_recorded(gw):
    register_run("g2", "econo", "observe", host="omnigent:gemini")
    status, reply = post(f"{gw}/run/g2/gemini{MODEL}:streamGenerateContent?alt=sse", BODY)
    chunks = [json.loads(line[6:]) for line in reply.split(b"\r\n") if line.startswith(b"data: ")]
    assert status == 200 and chunks == CHUNKS
    assert Upstream.seen[0][0] == f"{MODEL}:streamGenerateContent?alt=sse"
    assert db().rows("SELECT uncached_input FROM outcomes WHERE run_id='g2'")[0][0] == 400


def test_the_real_key_replaces_the_placeholder_and_never_reaches_the_log(gw, tmp_path):
    register_run("g3", "baseline", "observe", host="omnigent:gemini")
    post(f"{gw}/run/g3/gemini{MODEL}:generateContent?key=econo-placeholder&x=1", BODY,
         {"Authorization": "Bearer econo-placeholder", "X-Goog-Api-Client": "gl-node/25"})
    path, headers, _ = Upstream.seen[0]
    headers = {k.lower(): v for k, v in headers.items()}
    assert headers["x-goog-api-key"] == "real-key" and "authorization" not in headers
    assert headers["x-goog-api-client"] == "gl-node/25"  # the harness's own headers pass
    assert path == f"{MODEL}:generateContent?x=1"  # a key in the query is removed
    logged = eventually((tmp_path / "logs" / "calls.jsonl").read_text)
    assert "real-key" not in logged and "econo-placeholder" not in logged


def test_other_gemini_paths_pass_through_without_being_counted(gw):
    register_run("g4", "baseline", "observe", host="omnigent:gemini")
    assert post(f"{gw}/run/g4/gemini{MODEL}:countTokens", BODY) == (200, b'{"totalTokens": 7}')
    assert post(f"{gw}/run/g4/gemini/v1beta1/something/new", BODY) == (200, b"not json")
    assert len(Upstream.seen) == 2
    assert not db().rows("SELECT * FROM outcomes WHERE run_id='g4'")
    assert not db().rows("SELECT * FROM runtime_spans WHERE run_id='g4'")


def test_a_provider_error_is_relayed_and_not_recorded(gw):
    register_run("g5", "baseline", "observe", host="omnigent:gemini")
    status, reply = post(f"{gw}/run/g5/gemini/v1beta1/publishers/google/models/bad-model"
                         f":generateContent", BODY)
    assert status == 429 and b"quota" in reply
    assert not db().rows("SELECT * FROM outcomes WHERE run_id='g5'")
    assert db().rows("SELECT status FROM runtime_spans WHERE run_id='g5'")[0][0] == "failed"


def test_the_run_id_may_come_from_a_header(gw):
    register_run("g6", "baseline", "observe", host="omnigent:gemini", current=False)
    post(f"{gw}/current/gemini{MODEL}:generateContent", BODY, {"X-Econo-Run-ID": "g6"})
    assert db().rows("SELECT COUNT(*) FROM outcomes WHERE run_id='g6'")[0][0] == 1


def test_unregistered_runs_are_refused_and_caps_apply(gw):
    assert post(f"{gw}/run/nope/gemini{MODEL}:generateContent", BODY)[0] == 400
    register_run("g7", "baseline", "observe", host="omnigent:gemini",
                 overrides={"limits": {"max_model_calls": 1}})
    assert post(f"{gw}/run/g7/gemini{MODEL}:generateContent", BODY)[0] == 200
    assert post(f"{gw}/run/g7/gemini{MODEL}:generateContent", BODY)[0] == 429
    assert len(Upstream.seen) == 1


def test_each_call_records_what_it_carried_but_not_the_prompt(gw):
    register_run("g9", "econo", "observe", host="omnigent:gemini")
    history = {**BODY, "contents": BODY["contents"] + [
        {"role": "model", "parts": [{"functionCall": {"name": "read_file", "id": "1",
                                                      "args": {"file_path": "a.py"}}}]},
        {"role": "user", "parts": [{"functionResponse": {"name": "read_file", "id": "1",
                                                         "response": {"output": "SECRET"}}}]}]}
    post(f"{gw}/run/g9/gemini{MODEL}:generateContent", BODY)
    post(f"{gw}/run/g9/gemini{MODEL}:generateContent", history)
    spans = [json.loads(r[0]) for r in db().rows(
        "SELECT metadata FROM runtime_spans WHERE run_id='g9' ORDER BY started_at")]
    assert [s["call_no"] for s in spans] == [0, 1]
    assert spans[0]["context_key"] == spans[1]["context_key"]
    assert [(f["name"], f["bytes"]) for f in spans[1]["function_responses"]] == [("read_file", 6)]
    assert spans[1]["response_calls"] == [] and spans[1]["response_text"] is True
    assert "SECRET" not in json.dumps(spans)


def test_an_unreadable_body_is_still_forwarded_unchanged(gw):
    register_run("g10", "baseline", "observe", host="omnigent:gemini")
    assert post(f"{gw}/run/g10/gemini{MODEL}:generateContent", b"not json")[0] == 200
    assert Upstream.seen[0][2] == b"not json"
    assert db().rows("SELECT COUNT(*) FROM outcomes WHERE run_id='g10'")[0][0] == 1


def test_the_econo_arm_records_evidence_and_the_baseline_does_not(gw, tmp_path):
    (tmp_path / "a.py").write_text("print(1)\n")
    history = {**BODY, "contents": BODY["contents"] + [
        {"role": "model", "parts": [{"functionCall": {"name": "read_file", "id": "1",
                                                      "args": {"file_path": "a.py"}}}]},
        {"role": "user", "parts": [{"functionResponse": {"name": "read_file", "id": "1",
                                                         "response": {"output": "print(1)"}}}]}]}
    for run, arm in (("e1", "econo"), ("b1", "baseline")):
        register_run(run, arm, "observe", host="omnigent:gemini", workdir=str(tmp_path))
        post(f"{gw}/run/{run}/gemini{MODEL}:generateContent", history)
    rows = db().rows("SELECT run_id, call_no, event, source_key FROM evidence_events")
    assert [tuple(r) for r in rows] == [("e1", 0, "acquired", "a.py")]


def test_a_retried_request_is_marked_and_its_evidence_not_counted_again(gw, tmp_path):
    (tmp_path / "a.py").write_text("print(1)\n")
    history = {**BODY, "contents": BODY["contents"] + [
        {"role": "model", "parts": [{"functionCall": {"name": "read_file", "id": "1",
                                                      "args": {"file_path": "a.py"}}}]},
        {"role": "user", "parts": [{"functionResponse": {"name": "read_file", "id": "1",
                                                         "response": {"output": "print(1)"}}}]}]}
    register_run("r1", "econo", "observe", host="omnigent:gemini", workdir=str(tmp_path))
    post(f"{gw}/run/r1/gemini{MODEL}:generateContent", history)
    post(f"{gw}/run/r1/gemini{MODEL}:generateContent", history)  # Gemini retrying the call
    assert db().rows("SELECT COUNT(*) FROM evidence_events WHERE run_id='r1'")[0][0] == 1
    retry = json.loads(db().rows("SELECT metadata FROM runtime_spans WHERE run_id='r1' "
                                 "ORDER BY started_at")[1][0])
    assert (retry["call_no"], retry["retry_of"]) == (1, 0)


def test_a_model_without_a_price_card_is_recorded_as_incomplete(gw):
    register_run("g8", "baseline", "observe", host="omnigent:gemini")
    post(f"{gw}/run/g8/gemini/v1beta1/publishers/google/models/gemini-9-lite:generateContent",
         BODY)
    outcome = db().rows("SELECT cost_usd, cost_complete FROM outcomes WHERE run_id='g8'")[0]
    assert tuple(outcome) == (None, 0)  # unknown, never priced as the run's model
