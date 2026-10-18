"""pflop_accuracy/check_pflops.py on a synthetic batch: one CLM run on our vLLM, logged as the gateway logs it, with the
server's ids and cached tokens. No network, no model."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pflop_accuracy"))

import check_pflops  # noqa: E402
from measure.cache_sim import cached_prefix  # noqa: E402
from measure.flops import prompt_ids  # noqa: E402
from measure.models import MEASURED  # noqa: E402
from measure.tokens import tokenizer  # noqa: E402


def test_a_vllm_batch_is_checked_and_its_table_written(tmp_path, monkeypatch):
    tok = tokenizer(MEASURED, server="vllm")
    template = {"reasoning_effort": "medium", "chat_template_kwargs": {"enable_thinking": True}}
    messages = [{"role": "system", "content": "You are helpful. " * 40}, {"role": "user", "content": "First question?"}]
    requests = [{"model": "qwen3.8:27b", "messages": messages, **template},
                {"model": "qwen3.8:27b", "messages": messages + [{"role": "assistant", "content": "An answer."},
                                                                 {"role": "user", "content": "Second?"}], **template}]
    server = [prompt_ids(tok, r) for r in requests]
    real = cached_prefix(server)                               # a server whose cache behaves as our simulator says
    label = "bcp-pflop-vllm-test"
    run_dir = tmp_path / "runs" / label / "clm" / "1-r1"
    log = tmp_path / "gateway" / f"{label}.clm.1.r1" / "calls.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("".join(json.dumps({"provider": "vllm", "call_no": i + 1, "status": 200, "request": r,
                                       "response": {"prompt_token_ids": s},
                                       "usage": {"prompt_tokens": len(s), "completion_tokens": 9,
                                                 "prompt_tokens_details": {"cached_tokens": c}}}) + "\n"
                           for i, (r, s, c) in enumerate(zip(requests, server, real))))
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps({"run_id": f"{label}.clm.1.r1", "task": "1",
                                                      "calls_log": str(log), "trial_dir": str(tmp_path / "none")}))
    monkeypatch.setattr(check_pflops, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(check_pflops, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(sys, "argv", ["check_pflops.py", "--label", label])
    check_pflops.main()
    table = (tmp_path / "results" / f"{label}.md").read_text()
    assert "2 of 2 calls validated" in table and "All match" in table
    assert "equals vLLM's cached tokens on 2 of 2 calls (100.0%)" in table and "(ratio 1.0000)" in table
