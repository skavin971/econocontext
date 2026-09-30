"""The Anthropic route (Claude Code) against a fake upstream: pass-through, usage, the key,
evidence, and the dollar budgets."""

import json
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

import pytest

import omnigent_layer
from econocontext.store.db import AgentDB
from omnigent_layer import anthropic_wire, gateway, register_run
from test_gateway import serve

CLAUDE = {"model": {"provider": "anthropic", "name": "claude-sonnet-5"}}
USAGE = {"input_tokens": 100, "cache_read_input_tokens": 20000, "cache_creation_input_tokens": 3000,
         "cache_creation": {"ephemeral_5m_input_tokens": 2000, "ephemeral_1h_input_tokens": 1000},
         "output_tokens": 50}
REPLY = {"type": "message", "role": "assistant", "model": "claude-sonnet-5",
         "content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn", "usage": USAGE}
EVENTS = [
    {"type": "message_start", "message": {"type": "message", "role": "assistant", "content": [],
                                          "usage": {**USAGE, "output_tokens": 1}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "hm"}},
    {"type": "content_block_start", "index": 1,
     "content_block": {"type": "tool_use", "id": "t9", "name": "Read", "input": {}}},
    {"type": "content_block_delta", "index": 1,
     "delta": {"type": "input_json_delta", "partial_json": '{"file_path": "a.py"}'}},
    {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 50}},
    {"type": "message_stop"},
]
BODY = {"model": "claude-sonnet-5", "max_tokens": 64000, "system": [{"type": "text", "text": "s"}],
        "messages": [{"role": "user", "content": "task"}]}


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

    refuse_changes = False  # answer 400 to a request carrying a pointer (like a history check)

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Upstream.seen.append((self.path, dict(self.headers), body))
        if Upstream.refuse_changes and b"Full output:" in body:
            return self.reply(400, b'{"type":"error","error":{"type":"invalid_request_error",'
                                   b'"message":"history was edited"}}')
        if self.path.startswith("/v1/messages/count_tokens"):
            return self.reply(200, b'{"input_tokens": 7}')
        if json.loads(body).get("stream"):
            sse = b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in EVENTS)
            return self.reply(200, sse, "text/event-stream")
        return self.reply(200, json.dumps(REPLY).encode())


@pytest.fixture
def gw(monkeypatch):
    Upstream.seen, Upstream.refuse_changes = [], False
    upstream = serve(Upstream)
    monkeypatch.setattr(gateway, "ANTHROPIC_UPSTREAM", f"http://127.0.0.1:{upstream.server_port}")
    monkeypatch.setenv("ECONOCONTEXT_ANTHROPIC_KEY", "real-anthropic-key")
    gateway.Gateway.db = AgentDB(omnigent_layer.DB_PATH)
    gateway.Gateway.db.execute(gateway.POINTERS_TABLE)
    server = serve(gateway.Gateway)
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    upstream.shutdown()


def post(url, body, headers=None) -> tuple[int, bytes]:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    request = urllib.request.Request(url, raw, {"Content-Type": "application/json",
                                                "x-api-key": "econo-placeholder",
                                                "anthropic-version": "2023-06-01",
                                                "anthropic-beta": "claude-code-20250219",
                                                **(headers or {})})
    try:
        reply = urllib.request.urlopen(request, timeout=30)
        return reply.status, reply.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def db():
    return AgentDB(omnigent_layer.DB_PATH)


def register(run, arm="baseline", **limits):
    overrides = {**CLAUDE, "limits": limits} if limits else CLAUDE
    register_run(run, arm, "observe", host="omnigent:claude-code", overrides=overrides)


def test_a_call_is_forwarded_byte_for_byte_with_the_real_key(gw):
    register("a1")
    raw = json.dumps(BODY).encode() + b"  "
    status, reply = post(f"{gw}/run/a1/anthropic/v1/messages?beta=true", raw)
    assert status == 200 and json.loads(reply) == REPLY
    path, headers, body = Upstream.seen[0]
    headers = {k.lower(): v for k, v in headers.items()}
    assert path == "/v1/messages?beta=true" and body == raw
    assert headers["x-api-key"] == "real-anthropic-key"
    assert headers["anthropic-beta"] == "claude-code-20250219"  # Claude Code's own headers pass


def test_streamed_usage_is_priced_with_both_cache_writes(gw):
    register("a2")
    post(f"{gw}/run/a2/anthropic/v1/messages", {**BODY, "stream": True})
    o = db().rows("SELECT * FROM outcomes WHERE run_id='a2'")[0]
    assert (o["uncached_input"], o["cache_read"], o["cache_write"], o["output"]) == (100, 20000, 2000, 50)
    # $2 in, $0.20 read, $2.50 5m write, $4 1h write, $10 out, per million tokens
    expected = (100 * 2 + 20000 * 0.2 + 2000 * 2.5 + 1000 * 4 + 50 * 10) / 1e6
    assert o["cost_complete"] == 1 and o["cost_usd"] == pytest.approx(expected)
    meta = json.loads(db().rows("SELECT metadata FROM runtime_spans WHERE run_id='a2'")[0][0])
    assert [c["name"] for c in meta["response_calls"]] == ["Read"] and meta["response_text"] is False


def test_count_tokens_passes_through_uncounted(gw):
    register("a3")
    assert post(f"{gw}/run/a3/anthropic/v1/messages/count_tokens", BODY) == (200, b'{"input_tokens": 7}')
    assert not db().rows("SELECT * FROM outcomes WHERE run_id='a3'")


def test_evidence_from_a_claude_code_read(gw, tmp_path):
    (tmp_path / "a.py").write_text("print(1)\n")
    register_run("a4", "econo", "observe", host="omnigent:claude-code", workdir=str(tmp_path),
                 overrides=CLAUDE)
    history = {**BODY, "messages": BODY["messages"] + [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read",
                                           "input": {"file_path": str(tmp_path / "a.py")}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                      "content": [{"type": "text", "text": "1\tprint(1)"}]}]}]}
    post(f"{gw}/run/a4/anthropic/v1/messages", history)
    rows = db().rows("SELECT event, source_key FROM evidence_events WHERE run_id='a4'")
    assert [tuple(r) for r in rows] == [("acquired", "a.py")]


def test_a_run_stops_at_its_dollar_budget(gw):
    register("a5", per_instance_budget_usd=0.00001)
    assert post(f"{gw}/run/a5/anthropic/v1/messages", BODY)[0] == 200
    time.sleep(0.2)  # the outcome is recorded just after the reply
    status, reply = post(f"{gw}/run/a5/anthropic/v1/messages", BODY)
    assert status == 429 and b"budget" in reply and len(Upstream.seen) == 1


def test_the_key_stops_at_its_total_budget(gw, monkeypatch):
    monkeypatch.setattr(gateway, "ANTHROPIC_BUDGET_USD", 0.00001)
    register("a6")
    post(f"{gw}/run/a6/anthropic/v1/messages", BODY)
    time.sleep(0.2)
    register("a7")  # another run on the same key
    status, reply = post(f"{gw}/run/a7/anthropic/v1/messages", BODY)
    assert status == 429 and b"Anthropic key" in reply


def test_the_econo_arm_logs_a_planner_decision_and_sends_the_request_unchanged(gw):
    register("a8", arm="econo")
    raw = json.dumps(BODY).encode()
    assert post(f"{gw}/run/a8/anthropic/v1/messages", raw)[0] == 200
    assert Upstream.seen[0][2] == raw
    [d] = db().rows("SELECT intercept, applied, decision_id FROM decisions WHERE run_id='a8'")
    assert (d["intercept"], d["applied"]) == ("plan_prompt", 0)
    time.sleep(0.2)
    assert db().rows("SELECT decision_id FROM outcomes WHERE run_id='a8'")[0][0] == d["decision_id"]


AUTOPILOT = {**CLAUDE, "allowlist": {"COMMIT_PENDING": True, "ZONED": False},
             "constraints": {"max_quality_risk": 0.2}}
BIG = "\n".join(f"line {i}: " + "x" * 80 for i in range(150))


def old_result_history():
    return {**BODY, "messages": [
        {"role": "user", "content": "fix it"},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "", "signature": "sig"},
                                          {"type": "tool_use", "id": "t1", "name": "Read",
                                           "input": {"file_path": "a.py"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": BIG}]},
        {"role": "assistant", "content": [{"type": "text", "text": "looked at it"}]},
        {"role": "user", "content": "go on"}]}


def test_autopilot_points_out_an_old_result_and_keeps_it_pointed_out(gw, tmp_path):
    register_run("p1", "econo", "autopilot", workdir=str(tmp_path), host="omnigent:claude-code",
                 overrides=AUTOPILOT)
    history = old_result_history()
    for turn in range(2):
        assert post(f"{gw}/run/p1/anthropic/v1/messages", history)[0] == 200
        sent = json.loads(Upstream.seen[-1][2])
        result = sent["messages"][2]["content"][0]
        assert "Full output:" in result["content"][0]["text"] and BIG not in json.dumps(result), turn
        assert sent["messages"][1] == history["messages"][1]  # the thinking turn is untouched
        assert [m["role"] for m in sent["messages"]] == [m["role"] for m in history["messages"]]
    [pointer] = list((tmp_path / ".econocontext" / "pointers").iterdir())
    assert pointer.read_text() == BIG
    assert db().rows("SELECT applied FROM decisions WHERE run_id='p1' AND chosen='COMMIT_PENDING'")


def test_a_refused_change_sends_the_original_and_stops_changing_the_run(gw, tmp_path):
    Upstream.refuse_changes = True
    register_run("p2", "econo", "autopilot", workdir=str(tmp_path), host="omnigent:claude-code",
                 overrides=AUTOPILOT)
    history = old_result_history()
    status, reply = post(f"{gw}/run/p2/anthropic/v1/messages", history)
    assert status == 200 and json.loads(reply) == REPLY           # the harness never sees the 400
    assert json.loads(Upstream.seen[-1][2]) == history            # the original went through
    post(f"{gw}/run/p2/anthropic/v1/messages", history)
    assert json.loads(Upstream.seen[-1][2]) == history            # and no change is tried again
    assert db().rows("SELECT 1 FROM gateway_pointers WHERE run_id='p2' AND tool_call_id='*stopped*'")


def test_a_model_other_than_sonnet_5_is_refused_not_sent(gw):
    register("m1")
    status, reply = post(f"{gw}/run/m1/anthropic/v1/messages", {**BODY, "model": "claude-fable-5-1"})
    assert status == 429 and b"not allowed" in reply and not Upstream.seen


def test_the_daily_token_cap_does_not_apply_to_the_dollar_budgeted_route(gw, monkeypatch):
    from omnigent_layer import gateway_common
    monkeypatch.setattr(gateway_common, "MAX_INPUT_TOKENS_PER_DAY", 1)
    register("t1")
    assert post(f"{gw}/run/t1/anthropic/v1/messages", BODY)[0] == 200
    time.sleep(0.2)
    assert post(f"{gw}/run/t1/anthropic/v1/messages", BODY)[0] == 200  # 20k cache reads later
