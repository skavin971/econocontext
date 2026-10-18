"""The gateway (gateway/server.py) in front of a fake provider: forwarding, the key, pacing, retries,
caps and the call log. No network beyond localhost."""

import http.client
import json
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

import pytest

from gateway.server import PROVIDERS, Gateway, Limiter
from tests.gateway.fakes import KEY, PINS, TEMPLATE, completion, log_rows, start

ROOT = Path(__file__).resolve().parents[2]

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
    assert sent == {"model": "qwen3.8:27b", "messages": expected, "tools": [], **PINS, "chat_template_kwargs": TEMPLATE}
    row = log_rows(s.log_dir, "r1")[0]
    assert row["request"] == sent                       # the log holds the body as sent, after the edits
    assert "removed earlier reasoning from 2 assistant message(s)" in row["edits"]
    assert "set max_tokens=2048 (client sent 64)" in row["edits"]


def test_pins_override_what_the_client_sent(stack):
    # Every pinned field, sent with a conflicting value: each is overwritten and the edit recorded.
    s = stack(lambda body: (200, OK))
    post(s.url, chat(temperature=1.0, top_p=1.0, max_tokens=8192, max_completion_tokens=4096, reasoning_effort="low",
                     chat_template_kwargs={"reasoning_effort": "low", "enable_thinking": False, "custom": 1}))
    sent = s.upstream.requests[0]["body"]
    assert {k: sent[k] for k in PINS} == PINS == {"reasoning_effort": "medium", "return_token_ids": True,
                                                  "temperature": 0.7, "top_p": 0.95, "max_tokens": 2048}
    assert "max_completion_tokens" not in sent
    assert sent["chat_template_kwargs"] == {"reasoning_effort": "medium", "enable_thinking": True, "custom": 1}
    assert log_rows(s.log_dir, "r1")[0]["edits"] == [
        "set reasoning_effort='medium' (client sent 'low')",
        "set chat_template_kwargs.reasoning_effort='medium' (client sent 'low')",
        "set return_token_ids=True",
        "set temperature=0.7 (client sent 1.0)",
        "set top_p=0.95 (client sent 1.0)",
        "set max_tokens=2048 (client sent 8192)",
        "removed max_completion_tokens (client sent 4096)",
        "set chat_template_kwargs.enable_thinking=True (client sent False)"]


def test_a_captured_clm_request_comes_out_with_the_same_values(tmp_path):
    # CLM's first request in tc9 (acl r1), as CLM sent it: the logged body minus the two fields the
    # step-9 gateway added (its edits). CLM already sends the sampling; only the cap and pins change.
    row = json.loads((ROOT / "runs/gateway/tc9.clm-32k.acl-permissions-inheritance.r1/calls.jsonl")
                     .read_text().splitlines()[0])
    assert row["edits"] == ["set reasoning_effort='medium'", "set return_token_ids=True"]
    clm = {k: v for k, v in row["request"].items() if k not in ("reasoning_effort", "return_token_ids")}
    assert (clm["temperature"], clm["top_p"], clm["max_tokens"], clm["chat_template_kwargs"]) == \
        (0.7, 0.95, 8192, {"enable_thinking": True})
    sent, edits = Gateway(PROVIDERS["purdue"], KEY, tmp_path).prepare(clm)
    assert sent == {**clm, **PINS, "chat_template_kwargs": TEMPLATE}
    assert (sent["temperature"], sent["top_p"], sent["max_tokens"], sent["reasoning_effort"]) == (0.7, 0.95, 2048, "medium")
    assert sent["messages"] == clm["messages"] and sent["tools"] == clm["tools"]
    assert edits == ["set reasoning_effort='medium'", "set return_token_ids=True", "set max_tokens=2048 (client sent 8192)"]


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
        assert row["request"] == {**chat(), **PINS, "chat_template_kwargs": TEMPLATE}
        assert row["usage"] == row["response"]["usage"]
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


def test_a_benchmark_gets_its_own_output_cap_by_run_id_prefix(stack):
    s = stack(lambda body: (200, OK), max_tokens_for={"bcp": 4096})
    post(s.url, chat(max_tokens=2048), run="bcp13p.base.q1.r1")
    post(s.url, chat(max_tokens=2048), run="tc9.raw.t.r1")
    assert s.upstream.requests[0]["body"]["max_tokens"] == 4096 and s.upstream.requests[1]["body"]["max_tokens"] == PINS["max_tokens"]
    assert log_rows(s.log_dir, "bcp13p.base.q1.r1")[0]["request"]["max_tokens"] == 4096


def test_a_judge_call_keeps_its_own_model_and_sampling_and_is_logged(stack):
    s = stack(lambda body: (200, OK))
    body = chat(model="qwen3:32b", temperature=0.7, top_p=0.8, max_tokens=4096)
    assert post(s.url, body, kind="judge", run="bcp13p.judge")[0] == 200
    assert s.upstream.requests[0]["body"] == body
    row = log_rows(s.log_dir, "bcp13p.judge")[0]
    assert row["kind"] == "judge" and row["edits"] == [] and row["request"] == body


def test_a_benchmarks_logs_go_to_its_own_directory_and_still_count(stack, tmp_path):
    elsewhere = tmp_path / "outside"
    s = stack(lambda body: (200, OK), log_dir_for={"bcp": elsewhere})
    post(s.url, chat(), run="bcp13p.base.q1.r1")
    post(s.url, chat(), run="tc9.raw.t.r1")
    assert (elsewhere / "bcp13p.base.q1.r1" / "calls.jsonl").exists()
    assert not (s.log_dir / "bcp13p.base.q1.r1").exists() and (s.log_dir / "tc9.raw.t.r1" / "calls.jsonl").exists()
    assert s.gateway.logged_today() == 2 and len(s.gateway.logged("bcp13p.base.q1.r1")) == 1


def test_the_vllm_provider_pins_requests_exactly_as_purdue_does_and_needs_no_key(tmp_path):
    # PFLOP accuracy (pflop_accuracy/): our own vLLM gets the same request as Purdue, so the two logs compare.
    from gateway.server import read_env
    body = chat(messages=[{"role": "user", "content": "hi"},
                          {"role": "assistant", "content": "ok", "reasoning_content": "thinking"}])
    purdue, _ = Gateway(PROVIDERS["purdue"], KEY, tmp_path).prepare(body)
    vllm, _ = Gateway(PROVIDERS["vllm"], "EMPTY", tmp_path).prepare(body)
    assert vllm == purdue and vllm["return_token_ids"] is True and "reasoning_content" not in vllm["messages"][1]
    assert read_env("NO_SUCH_VARIABLE", tmp_path / "missing.env", default="EMPTY") == "EMPTY"
    with pytest.raises(SystemExit):
        read_env("NO_SUCH_VARIABLE", tmp_path / "missing.env")
