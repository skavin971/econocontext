"""count_prompt (measure/tokens.py) against the server's own prompt_tokens, on logged tc9 calls.

Three runs with different shapes: raw on acl (short), CLM at 32k on scan-linux r3 (42 calls, one context
edit) and econo+jev on malicious-package r2 (a compaction, with its summary call). Every answered call's
count must equal the server's prompt_tokens; a difference is allowed only where the validation rule
accepts it (Purdue reordering tool-schema keys inside the tools block). Skipped when the tokenizer
isn't in the local Hugging Face cache (no download in tests).
"""

import json
from pathlib import Path

import pytest

from measure.flops import prompt_ids, validate
from measure.models import MEASURED, load
from measure.tokens import count_prompt

ROOT = Path(__file__).resolve().parents[2]
RUNS = ["tc9.raw.acl-permissions-inheritance.r1", "tc9.clm-32k.scan-linux-persistence-artifacts.r3",
        "tc9.econo+jev.malicious-package-forensics.r2"]


@pytest.fixture(scope="module")
def tokenizer():
    transformers = pytest.importorskip("transformers")
    try:
        return transformers.AutoTokenizer.from_pretrained(MEASURED, revision=load()["revision"], local_files_only=True)
    except OSError:
        pytest.skip("the tokenizer is not in the local Hugging Face cache")


def test_the_count_is_the_servers_prompt_tokens(tokenizer):
    checked, kinds, reordered = 0, set(), 0
    for run in RUNS:
        for line in (ROOT / "runs" / "gateway" / run / "calls.jsonl").read_text().splitlines():
            row = json.loads(line)
            if row.get("status") != 200 or not row.get("call_no"):
                continue
            request = row["request"]
            template = {k: request[k] for k in ("reasoning_effort", "chat_template_kwargs") if k in request}
            n = count_prompt(request["messages"], request.get("tools"), request["model"], **template)
            server = row["usage"]["prompt_tokens"]
            if n != server:
                ok, note, dP = validate(tokenizer, prompt_ids(tokenizer, request), row["response"]["prompt_token_ids"])
                assert ok and n - server == -dP, (run, row["call_no"], n, server, note)
                reordered += 1
            checked, kinds = checked + 1, kinds | {row["kind"]}
    assert checked == 5 + 42 + 14 and kinds == {"agent", "summary"}
    assert reordered < checked / 4          # most calls match exactly


def test_an_unknown_model_is_refused():
    with pytest.raises(ValueError, match="no measured constants"):
        count_prompt([{"role": "user", "content": "hi"}], None, "some-other-model")
