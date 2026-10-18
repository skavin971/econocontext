"""pflop_accuracy/ scripts without a model or network beyond localhost: run_clm.py's refusals, the vLLM cache probe
against a fake vLLM, and check_ready.py's report when nothing is running."""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pflop_accuracy"))

import check_ready  # noqa: E402
import probe_vllm_cache  # noqa: E402
import run_clm  # noqa: E402
from measure.cache_sim import cached_prefix  # noqa: E402


def call(module, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", [module.__name__, *argv])
    module.main()


def test_run_clm_refuses_a_reused_label_a_wrong_gateway_and_a_judge_on_vllm(tmp_path, monkeypatch):
    monkeypatch.setattr(run_clm, "RUNS", tmp_path)
    monkeypatch.setattr(run_clm, "run", lambda *a, **k: pytest.fail("nothing may run"))
    (tmp_path / "bcp-pflop-purdue-x").mkdir()
    with pytest.raises(SystemExit, match="already exists"):
        call(run_clm, monkeypatch, "--backend", "purdue", "--label", "bcp-pflop-purdue-x")
    with pytest.raises(SystemExit, match="start with"):
        call(run_clm, monkeypatch, "--backend", "purdue", "--label", "bcp13p")
    monkeypatch.setattr(run_clm, "get_json", lambda url: {"provider": "purdue"})
    with pytest.raises(SystemExit, match="must run provider vllm"):
        call(run_clm, monkeypatch, "--backend", "vllm", "--label", "bcp-pflop-vllm-y")
    monkeypatch.setattr(run_clm, "get_json", lambda url: {"provider": "vllm"})
    with pytest.raises(SystemExit, match="--judge needs Path A"):
        call(run_clm, monkeypatch, "--backend", "vllm", "--label", "bcp-pflop-vllm-z", "--judge")
    assert not (tmp_path / "bcp-pflop-vllm-z").exists()


def test_run_clm_runs_only_clm_under_its_own_label(tmp_path, monkeypatch):
    monkeypatch.setattr(run_clm, "RUNS", tmp_path)
    monkeypatch.setattr(run_clm, "get_json", lambda url: {"provider": "vllm"})
    commands = []
    monkeypatch.setattr(run_clm, "run", lambda command, **k: commands.append([str(c) for c in command]))
    call(run_clm, monkeypatch, "--backend", "vllm", "--questions", "2", "--label", "bcp-pflop-vllm-t")
    (command,) = commands
    assert command[1].endswith("benchmarks/bcp/run.py")
    assert command[command.index("--arms") + 1] == "clm" and command[command.index("--limit") + 1] == "2"
    assert command[command.index("--label") + 1] == "bcp-pflop-vllm-t"


class FakeVllm:
    """A vLLM stand-in: each prompt's token ids are its characters, and the cached tokens are given by a rule."""

    def __init__(self, cached):
        self.cached, self.seen = cached, []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                ids = [ord(ch) for m in body["messages"] for ch in m["role"] + m["content"]]
                fake.seen.append(ids)
                reply = {"prompt_token_ids": ids, "usage": {"prompt_tokens": len(ids), "completion_tokens": 1,
                                                            "prompt_tokens_details": {"cached_tokens": fake.cached(fake.seen)}}}
                raw = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"


@pytest.mark.parametrize("rule, verdict", [(lambda seen: cached_prefix(seen)[-1], "matches this server on all 4"),
                                           (lambda seen: 0, "differs from this server")])
def test_the_cache_probe_compares_vllms_counts_with_the_simulator(rule, verdict, monkeypatch, capsys):
    fake = FakeVllm(rule)
    try:
        call(probe_vllm_cache, monkeypatch, "--vllm", fake.url)
    finally:
        fake.server.shutdown()
    out = capsys.readouterr().out
    assert verdict in out and len(fake.seen) == 4
    assert fake.seen[1] == fake.seen[2] and fake.seen[3][:len(fake.seen[1]) - 1] == fake.seen[1][:-1]


def test_check_ready_reports_each_missing_service_with_its_fix(monkeypatch, capsys):
    monkeypatch.setattr(check_ready, "get_json", lambda url, timeout=5: None)
    call(check_ready, monkeypatch, "--backend", "vllm")
    out = capsys.readouterr().out
    assert "[FAIL] search service" in out and "[FAIL] gateway on :8787 with provider vllm" in out
    assert "--provider vllm --upstream" in out and "Not ready" in out
