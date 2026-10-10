"""The gateway (gateway/server.py) in front of a fake provider: forwarding, the key, pacing, retries,
caps and the call log. No network beyond localhost."""

import http.client
import json
import urllib.request
from urllib.error import HTTPError

import pytest

from gateway.server import Limiter
from tests.gateway.fakes import KEY, PINS, completion, log_rows, start

OK = completion({"role": "assistant", "content": "done"})


@pytest.fixture
def stack(tmp_path):
    running = []

    def build(reply, log_dir=None, **limits):
        s = start(reply, log_dir or tmp_path / "gateway", **limits)
        running.append(s)
        return s
    yield build
    for s in running:
        s.stop()


def post(url, body, kind=None, run="r1"):
    headers = {"Content-Type": "application/json", "Authorization": "Bearer placeholder"}
    if kind:
        headers["X-Econo-Call-Kind"] = kind
    request = urllib.request.Request(f"{url}/run/{run}/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()


def chat(**extra):
    return {"model": "qwen3.8:27b", "messages": [{"role": "user", "content": "hi"}], **extra}


def test_a_call_is_forwarded_with_the_pins_and_without_earlier_reasoning(stack):
    s = stack(lambda body: (200, OK))
    messages = [{"role": "user", "content": "list files"},
                {"role": "assistant", "content": None, "reasoning_content": "I will run ls.",
                 "provider_specific_fields": {"reasoning": "I will run ls.", "refusal": None},
                 "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "c1", "content": "a.txt"},
                {"role": "assistant", "content": "a.txt", "reasoning": "Done."},
                {"role": "user", "content": "thanks"}]
    status, raw = post(s.url, {"model": "qwen3.8:27b", "messages": messages, "tools": [], "max_tokens": 64})
    assert status == 200 and raw == OK                                    # the provider's bytes, unchanged
    sent = s.upstream.requests[0]["body"]
    expected = json.loads(json.dumps(messages))
    for m in expected:
        m.pop("reasoning_content", None), m.pop("reasoning", None), m.pop("provider_specific_fields", None)
    assert sent == {"model": "qwen3.8:27b", "messages": expected, "tools": [], "max_tokens": 64, **PINS}
    row = log_rows(s.log_dir, "r1")[0]
    assert row["request"] == sent                       # the log holds the body as sent, after the edits
    assert "removed earlier reasoning from 2 assistant message(s)" in row["edits"]


def test_pins_override_what_the_client_sent(stack):
    s = stack(lambda body: (200, OK))
    post(s.url, chat(reasoning_effort="low", chat_template_kwargs={"reasoning_effort": "low", "enable_thinking": True}))
    sent = s.upstream.requests[0]["body"]
    assert sent["reasoning_effort"] == "medium" and sent["return_token_ids"] is True
    assert sent["chat_template_kwargs"] == {"reasoning_effort": "medium", "enable_thinking": True}


def test_the_provider_key_replaces_the_client_key_and_is_never_logged(stack):
    s = stack(lambda body: (200, completion({"role": "assistant", "content": f"echo {KEY}"})))
    post(s.url, chat())
    assert s.upstream.requests[0]["headers"]["Authorization"] == f"Bearer {KEY}"
    text = (s.log_dir / "r1" / "calls.jsonl").read_text()
    assert KEY not in text and "[REDACTED]" in text


def test_a_null_body_is_retried_and_the_retry_is_recorded(stack):
    replies = iter([(200, b"null"), (200, b""), (200, OK)])
    s = stack(lambda body: next(replies))
    assert post(s.url, chat()) == (200, OK)
    assert len(s.upstream.requests) == 3 and log_rows(s.log_dir, "r1")[0]["retries"] == 2


def test_failures_stop_after_six_tries(stack):
    s = stack(lambda body: (503, b'{"error": "busy"}'))
    status, _ = post(s.url, chat())
    assert status == 503 and len(s.upstream.requests) == 6 and log_rows(s.log_dir, "r1")[0]["retries"] == 5
    t = stack(lambda body: (200, b"null"))
    status, raw = post(t.url, chat(), run="r2")
    assert status == 502 and b"upstream failed after 6 tries" in raw


def test_a_client_error_is_passed_through_without_retrying(stack):
    s = stack(lambda body: (400, b'{"error": {"message": "context too long"}}'))
    assert post(s.url, chat())[0] == 400 and len(s.upstream.requests) == 1


def test_every_upstream_attempt_is_paced(stack):
    replies = iter([(429, b"{}"), (200, OK)])
    s = stack(lambda body: next(replies))
    counted = []
    s.gateway.limiter.acquire = lambda: counted.append(1) or 0.0
    post(s.url, chat())
    assert len(counted) == 2                               # the retry counted against the limit too


def test_the_limiter_waits_for_the_oldest_request_to_leave_the_window():
    now, slept = [0.0], []

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds
    limiter = Limiter(3, window_s=60.0, clock=lambda: now[0], sleep=sleep)
    assert [limiter.acquire() for _ in range(3)] == [0.0, 0.0, 0.0]
    now[0] = 10.0
    assert limiter.acquire() == pytest.approx(50.0) and slept == [pytest.approx(50.0)]
    assert limiter.acquire() == 0.0                         # the window has room again


def test_caps_refuse_with_reached_and_are_logged(stack):
    s = stack(lambda body: (200, OK), max_calls_per_run=2)
    assert [post(s.url, chat())[0] for _ in range(2)] == [200, 200]
    status, raw = post(s.url, chat())
    assert status == 429 and b"reached" in raw and len(s.upstream.requests) == 2
    assert [r["call_no"] for r in log_rows(s.log_dir, "r1")] == [1, 2, None]
    t = stack(lambda body: (200, OK), log_dir=s.log_dir.parent / "other", max_requests_per_day=1)
    assert post(t.url, chat(), run="a")[0] == 200
    status, raw = post(t.url, chat(), run="b")
    assert status == 429 and b"daily request cap reached" in raw


def test_the_log_line_has_every_field(stack):
    s = stack(lambda body: (200, completion({"role": "assistant", "content": "x"}, extra={"prompt_token_ids": [1, 2]})))
    post(s.url, chat())
    post(s.url, chat(), kind="summary")
    first, second = log_rows(s.log_dir, "r1")
    for request in s.upstream.requests:                 # the kind is ours: it never goes upstream
        assert "x-econo-call-kind" not in {name.lower() for name in request["headers"]}
    for row in (first, second):
        assert {"run_id", "call_no", "kind", "provider", "model", "t_start", "latency_ms", "status", "retries",
                "request", "response", "usage"} <= set(row)
        assert row["request"] == {**chat(), **PINS} and row["usage"] == row["response"]["usage"]
    assert (first["call_no"], first["kind"], second["call_no"], second["kind"]) == (1, "agent", 2, "summary")
    assert first["run_id"] == "r1" and first["model"] == "qwen3.8:27b" and first["response"]["prompt_token_ids"] == [1, 2]


def test_counts_continue_after_a_restart(stack):
    s = stack(lambda body: (200, OK), max_calls_per_run=3)
    post(s.url, chat()), post(s.url, chat())
    again = stack(lambda body: (200, OK), max_calls_per_run=3)   # a new gateway on the same log
    assert post(again.url, chat())[0] == 200 and post(again.url, chat())[0] == 429
    assert [r["call_no"] for r in log_rows(again.log_dir, "r1")] == [1, 2, 3, None]


def test_streaming_unknown_paths_and_bad_run_ids_are_refused(stack):
    s = stack(lambda body: (200, OK))
    status, raw = post(s.url, chat(stream=True))
    assert status == 400 and b"streaming not supported" in raw
    host, port = s.url.removeprefix("http://").split(":")
    for path in ("/run/../x/v1/chat/completions", "/run/.hidden/v1/chat/completions", "/v1/chat/completions"):
        connection = http.client.HTTPConnection(host, int(port), timeout=5)
        connection.request("POST", path, body=json.dumps(chat()), headers={"Content-Type": "application/json"})
        assert connection.getresponse().status == 404
    assert s.upstream.requests == []


def test_health_reports_the_caps_and_todays_requests(stack):
    s = stack(lambda body: (200, OK), max_calls_per_run=150, max_requests_per_day=8000)
    post(s.url, chat()), post(s.url, chat(), run="r2")
    with urllib.request.urlopen(f"{s.url}/health", timeout=5) as response:
        health = json.load(response)
    assert (health["requests_today"], health["max_requests_per_day"], health["max_calls_per_run"]) == (2, 8000, 150)
