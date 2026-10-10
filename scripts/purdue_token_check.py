"""Does Qwen/Qwen3.8-27B-FP8's own tokenizer and chat template reproduce Purdue's prompts?

Why it exists: step 1's confidence check. The FLOPs measurement rebuilds every prompt's token ids
from the logged request, so it only works if (a) we have the right tokenizer, chat template and
template options and (b) the server templates exactly what we sent. This renders the smoke test's
saved request (runs/purdue-smoke/request.json) under every option of the template
(reasoning_effort xhigh/medium/low, thinking off), compares the counts with the server's
prompt_tokens, and, when the smoke test's token-id probe saved the server's own prompt token ids
(response-ids-*.json), compares them token by token and shows the text of any differing line.
Downloads tokenizer files only, never weights.

Run (needs transformers): .venv-clm/bin/python scripts/purdue_token_check.py
"""

import json
from pathlib import Path

from huggingface_hub import model_info
from transformers import AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]
HF_ID = "Qwen/Qwen3.8-27B-FP8"
OPTIONS = {"default (xhigh)": {}, "reasoning_effort=medium": {"reasoning_effort": "medium"},
           "reasoning_effort=low": {"reasoning_effort": "low"}, "enable_thinking=False": {"enable_thinking": False}}


def render(tokenizer, request: dict, tokenize: bool, **options):
    out = tokenizer.apply_chat_template(request["messages"], tools=request.get("tools"),
                                        add_generation_prompt=True, tokenize=tokenize, **options)
    if not tokenize:
        return out
    return list(out["input_ids"] if hasattr(out, "keys") else out)


def main() -> None:
    smoke = ROOT / "runs" / "purdue-smoke"
    request = json.loads((smoke / "request.json").read_text())
    served = [json.loads((smoke / name).read_text())["usage"]["prompt_tokens"]
              for name in ("response.json", "response-2.json")]
    revision = model_info(HF_ID).sha
    tokenizer = AutoTokenizer.from_pretrained(HF_ID, revision=revision)
    print(f"{HF_ID} at revision {revision}; tokenizer {type(tokenizer).__name__}, "
          f"chat template {len(tokenizer.chat_template or '')} chars")
    print(f"server prompt_tokens for the smoke request, two calls: {served}")
    for label, options in OPTIONS.items():
        print(f"  local {label:26s} {len(render(tokenizer, request, True, **options)):5d} tokens")

    probes = sorted(smoke.glob("response-ids-*.json"))
    if not probes:
        return
    medium = OPTIONS["reasoning_effort=medium"]
    local = render(tokenizer, request, True, **medium)
    (smoke / "local-render.txt").write_text(render(tokenizer, request, False, **medium))
    print(f"\nserver prompt token ids (return_token_ids) vs local reasoning_effort=medium ({len(local)} tokens):")
    local_lines = tokenizer.decode(local).split("\n")
    for path in probes:
        ids = json.loads(path.read_text())["prompt_token_ids"]
        print(f"  {path.name}: {len(ids)} tokens, identical: {ids == local}")
        for mine, theirs in zip(local_lines, tokenizer.decode(ids).split("\n")):
            if mine != theirs:
                print(f"    local : {mine}\n    server: {theirs}")
    print(f"local rendering (reasoning_effort=medium) saved to {(smoke / 'local-render.txt').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
