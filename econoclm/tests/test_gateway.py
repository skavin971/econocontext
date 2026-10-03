"""The measure-only gateway against a fake upstream HTTP server."""

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from econoclm.core import prices
from econoclm.core.gateway import make_server
from econoclm.core.gateway_ledger import Ledger


class Upstream(BaseHTTPRequestHandler):
    """Records what it received; replies from the `script` list (one reply per call)."""
    protocol_version = "HTTP/1.1"
    seen: list = []
    script: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        type(self).seen.append({"path": self.path, "body": raw, "headers": dict(self.headers)})
        status, ctype, body = type(self).script.pop(0)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        if ctype == "text/event-stream":
            self.send_header("Connection", "close")
            self.end_headers()
            for line in body:
                self.wfile.write(line)
            self.close_connection = True
            return
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def reply(usage, finish="stop"):
    return (200, "application/json", json.dumps({
        "id": "x", "object": "chat.completion",
        "choices": [{"index": 0, "finish_reason": finish,
                     "message": {"role": "assistant", "content": "hi"}}],
        "usage": usage}).encode())


@pytest.fixture
def servers(tmp_path):
    Upstream.seen, Upstream.script = [], []
    up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    gw = make_server(str(tmp_path / "gateway.sqlite"), port=0,
                     upstream=f"http://127.0.0.1:{up.server_address[1]}/openapi",
                     key="SECRET-KEY", max_spend_usd=40, max_inflight=4, prior_spend_usd=0)
    cfg = gw.RequestHandlerClass.cfg
    cfg.backoff_base_s = 0.01
    threading.Thread(target=gw.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{gw.server_address[1]}", cfg
    gw.shutdown()
    up.shutdown()


def rows(cfg, run_id, n=1, wait_s=3.0):
    """The run's ledger rows. The gateway writes a row just AFTER relaying the reply
    (the agent waits for it the same way), so poll briefly."""
    deadline = time.monotonic() + wait_s
    while True:
        got = cfg.ledger.calls(run_id)
        if len(got) >= n or time.monotonic() > deadline:
            return got
        time.sleep(0.02)


def post(url, raw: bytes, auth="Bearer placeholder"):
    req = urllib.request.Request(url, raw, {"Content-Type": "application/json",
                                            "Authorization": auth})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


# A body with odd spacing and key order: forwarded byte for byte, never re-encoded.
BODY = b'{"model":  "google/gemini-3.6-flash", "messages":[{"role":"user","content":"x \\u00e9"}],"max_tokens":2048 }'


def test_forwards_bytes_and_records_usage(servers):
    gw, cfg = servers
    usage = {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1150,
             "prompt_tokens_details": {"cached_tokens": 600},
             "completion_tokens_details": {"reasoning_tokens": 50}}
    Upstream.script = [reply(usage)]
    status, body = post(f"{gw}/run/r1/v1/chat/completions", BODY)
    assert status == 200 and json.loads(body)["choices"][0]["message"]["content"] == "hi"
    seen = Upstream.seen[0]
    assert seen["body"] == BODY
    assert seen["path"] == "/openapi/chat/completions"
    headers = {k.lower(): v for k, v in seen["headers"].items()}
    assert headers.get("x-goog-api-key") == "SECRET-KEY"
    assert "placeholder" not in json.dumps(seen["headers"])  # the agent's key is not passed on
    row = rows(cfg, "r1")[0]
    assert (row["prompt_tokens"], row["cached_tokens"], row["uncached_tokens"]) == (1000, 600, 400)
    assert row["output_tokens"] == 150  # reasoning reported outside completion_tokens
    assert row["reasoning_tokens"] == 50
    expected = 400 * prices.PRICE_IN + 600 * prices.PRICE_CACHED + 150 * prices.PRICE_OUT
    assert row["cost_usd"] == pytest.approx(expected)
    assert row["finish_reason"] == "stop" and row["http_status"] == 200
    assert row["upstream_attempts"] == 1 and row["ratelimit_wait_ms"] == 0
    assert row["cached_reported"] == 1 and row["latency_ms"] >= 0


def test_missing_prompt_details_means_zero_cached(servers):
    gw, cfg = servers
    Upstream.script = [reply({"prompt_tokens": 500, "completion_tokens": 10, "total_tokens": 510})]
    post(f"{gw}/run/r2/v1/chat/completions", BODY)
    row = rows(cfg, "r2")[0]
    assert row["cached_tokens"] == 0 and row["uncached_tokens"] == 500
    assert row["cached_reported"] == 0
    assert row["cost_usd"] > 0


def test_streaming_asks_for_usage_and_relays_lines(servers):
    gw, cfg = servers
    chunks = [
        b'data: {"choices":[{"index":0,"delta":{"content":"h"}}]}\n\n',
        b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"length"}]}\n\n',
        b'data: {"choices":[],"usage":{"prompt_tokens":40,"completion_tokens":5,"total_tokens":45}}\n\n',
        b"data: [DONE]\n\n",
    ]
    Upstream.script = [(200, "text/event-stream", chunks)]
    body = json.dumps({"model": "m", "stream": True, "messages": []}).encode()
    status, got = post(f"{gw}/run/r3/v1/chat/completions", body)
    assert status == 200 and got == b"".join(chunks)
    sent = json.loads(Upstream.seen[0]["body"])
    assert sent["stream_options"] == {"include_usage": True}
    row = rows(cfg, "r3")[0]
    assert row["prompt_tokens"] == 40 and row["finish_reason"] == "length" and row["stream"] == 1


def test_rate_limited_calls_are_retried_with_same_bytes(servers):
    gw, cfg = servers
    quota = (429, "application/json", b'{"error":{"code":429,"message":"quota"}}')
    Upstream.script = [quota, quota, reply({"prompt_tokens": 5, "completion_tokens": 1,
                                            "total_tokens": 6})]
    status, _ = post(f"{gw}/run/r4/v1/chat/completions", BODY)
    assert status == 200
    assert [s["body"] for s in Upstream.seen] == [BODY] * 3
    got = rows(cfg, "r4")
    assert len(got) == 1  # the agent saw one call
    assert got[0]["upstream_attempts"] == 3 and got[0]["ratelimit_wait_ms"] > 0


def test_rate_limit_retry_gives_up_after_budget(servers):
    gw, cfg = servers
    cfg.retry_budget_s = 0.05
    quota = (429, "application/json", b'{"error":{"code":429,"message":"quota"}}')
    Upstream.script = [quota] * 50
    status, body = post(f"{gw}/run/r5/v1/chat/completions", BODY)
    assert status == 429 and b"quota" in body
    assert rows(cfg, "r5")[0]["http_status"] == 429


def test_spend_cap_refuses_new_calls(servers):
    gw, cfg = servers
    cfg.ledger.insert("old", cost_usd=40.01)
    status, body = post(f"{gw}/run/r6/v1/chat/completions", BODY)
    assert status == 429 and b"SPEND CAP" in body
    assert Upstream.seen == []


def test_unknown_path_is_refused(servers):
    gw, _ = servers
    assert post(f"{gw}/run/r7/v1/responses", BODY)[0] == 404
    assert Upstream.seen == []


def test_listens_on_localhost_only(tmp_path):
    gw = make_server(str(tmp_path / "g.sqlite"), port=0, upstream="http://x", key="k")
    try:
        assert gw.server_address[0] == "127.0.0.1"
    finally:
        gw.server_close()


def test_smoke_tool_against_fake_upstream(servers, tmp_path, capsys):
    from econoclm.bench import smoke
    gw, cfg = servers
    usage = {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 20,
             "completion_tokens_details": {"reasoning_tokens": 6}}
    Upstream.script = [reply(usage)]
    port = gw.rsplit(":", 1)[1]
    ledger_path = cfg.ledger.conn.execute("PRAGMA database_list").fetchone()[2]
    code = smoke.main(["--port", port, "--ledger", ledger_path])
    out = capsys.readouterr().out
    assert code == 0 and "GATE 2: PASS" in out
    assert json.loads(Upstream.seen[0]["body"])["model"] == "google/gemini-3.6-flash"
    assert "SECRET-KEY" not in out
    assert "prompt_tokens_details present: False" in out


def test_smoke_check_flags_problems():
    from econoclm.bench.smoke import check
    assert check(500, {"error": "x"}, None) == ["HTTP 500: x", "no ledger row"]
    row = {c: 1 for c in ("prompt_tokens", "cached_tokens", "uncached_tokens", "output_tokens",
                          "reasoning_tokens", "latency_ms", "finish_reason", "http_status")}
    assert check(200, {}, {**row, "cost_usd": 0.0}) == ["cost_usd is not > 0"]


def test_spend_cap_counts_other_phases(tmp_path):
    Ledger(tmp_path / "pilot" / "gateway.sqlite").insert("p", cost_usd=39.5)
    gw = make_server(str(tmp_path / "main" / "gateway.sqlite"), port=0, upstream="http://x", key="k")
    try:
        assert gw.RequestHandlerClass.cfg.prior_spend_usd == pytest.approx(39.5)
    finally:
        gw.server_close()
