r"""A prompt's size as the server will count it, for an agent to check before it sends.

Why it exists: a token budget (step 10c) is enforced on the prompt the server will see. This is the
same rebuild as measure/flops.py (vLLM's message parsing, the model's own chat template and the
request's template options), so the count is the server's prompt_tokens, apart from Purdue's random
reordering of tool-schema keys (a few tokens, inside the tools block; see docs/tier-a-decisions.md).
count_text is the same tokenizer on a piece of text (no template): EconoContext's token counts (step 12a),
so the method and the measure count with one function. Agents and the method may import this module;
measure/ never imports the method.

The tokenizer is the model's own, at the measured revision, with one change for the model Purdue serves
(step 13): Purdue's server splits letters from their combining marks (e.g. Devanagari), as if the pre-tokenizer
had no \p{M}. Every published revision of Qwen3.8-27B(-FP8) has [\p{L}\p{M}]+; with \p{M} removed, local ids equal
Purdue's on all 2,259 logged calls (TBLite 2,194, BrowseComp-Plus 65), against 2,249 without the change
(runs/tier-a/step13-tokenizer-check.out). So the counts and rebuilds match what the server computes.
A stock server (our own vLLM, Tier B: pflop_accuracy/) tokenizes as published, so tokenizer(..., server="vllm") is
the unpatched tokenizer; tokenizer_for_log picks the right one from a calls.jsonl's own "provider" field.

Use: count_prompt(messages, tools, "qwen3.8:27b", reasoning_effort="medium",
                  chat_template_kwargs={"enable_thinking": True})
     count_text("some text", "qwen3.8:27b")
"""

import functools

from .flops import prompt_ids
from .models import SERVED, load


SERVER_SPLITS_MARKS = set(SERVED.values())      # models Purdue serves: its pre-tokenizer has no \p{M} (step 13)


@functools.cache
def tokenizer(hf_id: str, server: str = "purdue", **kwargs):
    """The model's own tokenizer and chat template, at the revision measured in model_constants.json; for a model
    Purdue serves, with Purdue's pre-tokenizer (combining marks split off; see above). server: the provider whose
    tokenization to reproduce ("purdue", or "vllm" for a stock server)."""
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(hf_id, revision=load(hf_id)["revision"], **kwargs)
    if server == "purdue" and hf_id in SERVER_SPLITS_MARKS:
        import json
        from tokenizers import Tokenizer
        spec = json.loads(tok.backend_tokenizer.to_str())
        split = spec["pre_tokenizer"]["pretokenizers"][0]["pattern"]
        split["Regex"] = split["Regex"].replace("[\\p{L}\\p{M}]+", "\\p{L}+").replace("\\p{L}\\p{M}\\p{N}", "\\p{L}\\p{N}")
        assert "\\p{M}" not in split["Regex"], "the pre-tokenizer pattern changed: review the step-13 fix"
        tok.backend_tokenizer.pre_tokenizer = Tokenizer.from_str(json.dumps(spec)).pre_tokenizer
    return tok


def tokenizer_for_log(calls_log, hf_id: str):
    """The tokenizer that reproduces the server of one run's calls.jsonl (its rows' "provider"; Purdue if absent)."""
    import json
    from pathlib import Path
    provider = "purdue"
    for line in Path(calls_log).read_text().splitlines():
        if line.strip():
            provider = json.loads(line).get("provider") or "purdue"
            break
    return tokenizer(hf_id, server=provider)


def resolve(model: str) -> str:
    """The Hugging Face id of a provider's model id (or of a Hugging Face id) that measure/ has measured."""
    hf_id = SERVED.get(model, model)
    try:
        load(hf_id)
    except KeyError:
        raise ValueError(f"no measured constants for model {model!r}: add it to measure/ first") from None
    return hf_id


def count_text(text: str, model: str) -> int:
    """Tokens of a text by the model's own tokenizer, without special tokens or a chat template."""
    return len(tokenizer(resolve(model)).encode(text, add_special_tokens=False)) if text else 0


def count_prompt(messages: list[dict], tools: list[dict] | None, model: str, **template) -> int:
    """Prompt tokens of a request. `model` is the provider's id (e.g. qwen3.8:27b) or a Hugging Face id;
    `template` holds the request's template options (reasoning_effort, chat_template_kwargs)."""
    return len(prompt_ids(tokenizer(resolve(model)), {"messages": messages, "tools": tools, **template}))
