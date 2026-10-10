"""The prompt rebuild against Purdue's own prompt token ids, with the model's real tokenizer.

Every saved request with the server's ids is rebuilt (measure/flops.py) and validated by Kavin's
rule. The saved requests are the step-1 smoke and probes and the step-3 live call through the
gateway. Each must pass, except the probe whose assistant turn carried `reasoning`: vLLM would
render it, Purdue's front end stripped it, and the gateway now strips it before sending. That
mismatch must be caught. Skipped when the tokenizer isn't in the local Hugging Face cache (no
download in tests).
"""

import json
from pathlib import Path

import pytest

from measure.flops import prompt_ids, validate
from measure.models import MEASURED, load

ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "runs" / "purdue-smoke"


@pytest.fixture(scope="module")
def tokenizer():
    transformers = pytest.importorskip("transformers")
    try:
        return transformers.AutoTokenizer.from_pretrained(MEASURED, revision=load()["revision"], local_files_only=True)
    except OSError:
        pytest.skip("the tokenizer is not in the local Hugging Face cache")


def saved_pairs():
    """(name, request, server ids) for every saved request whose response carries prompt_token_ids."""
    smoke = json.loads((SMOKE / "request.json").read_text())
    pairs = [(f"smoke {p.name}", {**smoke, "reasoning_effort": "medium"},
              json.loads(p.read_text())["prompt_token_ids"]) for p in sorted(SMOKE.glob("response-ids-*.json"))]
    for request in sorted((SMOKE / "template-probe").glob("request-*.json")):
        body = json.loads(request.read_text())
        response = json.loads(request.with_name(request.name.replace("request", "response")).read_text())
        if "reasoning_effort" not in body and "reasoning_effort" not in (body.get("chat_template_kwargs") or {}):
            body = {**body, "reasoning_effort": "medium"}              # the server's default, as measured
        pairs.append((f"probe {request.name}", body, response["prompt_token_ids"]))
    for line in (ROOT / "runs" / "gateway" / "tier-a.step3.live" / "calls.jsonl").read_text().splitlines():
        row = json.loads(line)
        pairs.append(("gateway live call", row["request"], row["response"]["prompt_token_ids"]))
    return pairs


def test_every_saved_prompt_is_rebuilt_exactly_or_differs_only_in_the_tools_block(tokenizer):
    results = {name: validate(tokenizer, prompt_ids(tokenizer, body), server) for name, body, server in saved_pairs()}
    reasoning_probe = [n for n in results if n.endswith("request-6.json")]
    assert reasoning_probe and not results[reasoning_probe[0]][0], "the stripped-`reasoning` probe must be caught"
    # The step-3 live call: Purdue reordered the keys of three tool schemas, so dP = -2. The rule as
    # decided (|dP| <= 1) rejects it; this is open for Kavin's decision (step-4 report).
    live = results.pop("gateway live call")
    assert live == (False, "tools block only (same tools, keys reordered), but dP -2 exceeds the allowed 1")
    others = {n: r for n, r in results.items() if n not in reasoning_probe}
    assert all(ok for ok, _ in others.values()), {n: note for n, (ok, note) in others.items() if not ok}
    assert any(note == "identical" for _, note in others.values())    # exact whenever Purdue did not reorder keys
