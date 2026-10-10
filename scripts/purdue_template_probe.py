"""Which chat-template options does Purdue apply to multi-turn prompts, and can a request pin them?

Why it exists: the FLOPs rebuild renders every logged request locally, so it must use the server's
exact template options. After step 1 two are open:
  preserve_thinking  whether an earlier assistant turn's reasoning_content is rendered in later
                     prompts. The template drops it (when false) only for assistant turns before the
                     last real user message, so two conversation shapes are probed: a tool turn
                     (user, assistant with reasoning and a tool call, tool result) and a follow-up
                     (user, assistant with reasoning and text, user).
  reasoning_effort   the server's default is medium; does a request field reach the template?
                     Probed with "low" (it changes the system prompt), as a top-level field and inside
                     chat_template_kwargs, then "medium".
Every call asks for the server's own prompt token ids (return_token_ids) and is compared with local
renders: identical, or (Kavin's rule) different only in the tools block before the first user
message with |dP| <= 1 (Purdue reorders write_file's schema keys at random), or different.
A sixth request sends the follow-up with the assistant's reasoning under vLLM's newer field name,
`reasoning`, to see whether the server reads it there. Requests are spaced under the 20-per-minute
limit. The key is read from .env, never printed, and redacted from saved files.

Run (needs transformers): .venv-clm/bin/python scripts/purdue_template_probe.py [--live all|1,3,...]
  --live picks the probes to send; the others are judged again from their saved request and response.
"""

import argparse

import copy
import json
import time
from pathlib import Path

from transformers import AutoTokenizer

from purdue_smoke import BASE, MODEL, PROMPT, ROOT, TOOLS, patient, read_key, save

HF_ID, REVISION = "Qwen/Qwen3.8-27B-FP8", "017b9c7af6b5689d5dd426a76e0bc077eb5ca20a"
OUT = ROOT / "runs" / "purdue-smoke" / "template-probe"


def conversations() -> dict:
    """The two shapes, built from the smoke test's real assistant message (echoed as our agent does)."""
    smoke = json.loads((ROOT / "runs" / "purdue-smoke" / "response.json").read_text())
    assistant = {k: v for k, v in smoke["choices"][0]["message"].items() if v is not None}
    call_id = assistant["tool_calls"][0]["id"]
    user = {"role": "user", "content": PROMPT}
    return {
        "tool turn": [user, assistant,
                      {"role": "tool", "tool_call_id": call_id, "content": "a.txt\nb.txt\n(exit code 0)"}],
        "follow-up": [user, {"role": "assistant", "content": "The directory has two files: a.txt and b.txt.",
                             "reasoning_content": "The user wants the files listed. I ran ls; there are two."},
                      {"role": "user", "content": "Now count them."}],
        "follow-up, field `reasoning`": [user, {"role": "assistant", "content": "The directory has two files: a.txt "
                                                "and b.txt.", "reasoning": "The user wants the files listed. I ran "
                                                "ls; there are two."},
                                         {"role": "user", "content": "Now count them."}],
    }


def for_template(messages: list[dict], drop_reasoning: bool = False) -> list[dict]:
    """vLLM parses each tool call's JSON `arguments` into a mapping before templating; do the same.
    drop_reasoning: remove the assistant turns' reasoning, as the server appears to."""
    out = copy.deepcopy(messages)
    for m in out:
        if drop_reasoning:
            m.pop("reasoning_content", None)
            m.pop("reasoning", None)
        for call in m.get("tool_calls") or []:
            if isinstance(call["function"].get("arguments"), str):
                call["function"]["arguments"] = json.loads(call["function"]["arguments"] or "{}")
    return out


def local_ids(tokenizer, messages, drop_reasoning=False, **options) -> list[int]:
    out = tokenizer.apply_chat_template(for_template(messages, drop_reasoning), tools=TOOLS,
                                        add_generation_prompt=True, tokenize=True, **options)
    return list(out["input_ids"] if hasattr(out, "keys") else out)


def verdict(tokenizer, server: list[int], local: list[int]) -> str:
    if server == local:
        return "identical"
    marker = tokenizer.encode("<|im_start|>user\n", add_special_tokens=False)
    def first_user(ids):
        return next((i for i in range(len(ids) - len(marker) + 1) if ids[i:i + len(marker)] == marker), None)
    s, l = first_user(server), first_user(local)
    if s is not None and l is not None and server[s:] == local[l:]:
        if abs(len(server) - len(local)) <= 1:
            return f"tools block only (dP = {len(server) - len(local):+d})"
        return f"DIFFERENT in the system block before the first user message (dP = {len(server) - len(local):+d})"
    tail_s, tail_l = server[s or 0:], local[l or 0:]
    at = next((k for k, (x, y) in enumerate(zip(tail_s, tail_l)) if x != y), min(len(tail_s), len(tail_l)))
    return (f"DIFFERENT after the first user message (P {len(server)} vs {len(local)}); at +{at}: "
            f"server {tokenizer.decode(tail_s[at:at + 12])!r} vs local {tokenizer.decode(tail_l[at:at + 12])!r}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--live", default="all", help="probes to send (all, or numbers like 6 or 1,3)")
    a = p.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    key = read_key(ROOT / ".env")
    tokenizer = AutoTokenizer.from_pretrained(HF_ID, revision=REVISION)
    base = {"model": MODEL, "tools": TOOLS, "max_tokens": 64, "temperature": 0.7, "stream": False,
            "return_token_ids": True}
    try:  # does a plain transformers render work without vLLM's argument parsing?
        tokenizer.apply_chat_template(conversations()["tool turn"], tools=TOOLS, add_generation_prompt=True,
                                      tokenize=False)
        print("unparsed tool-call arguments: transformers renders them")
    except Exception as exc:
        print(f"unparsed tool-call arguments: transformers fails ({type(exc).__name__}: {str(exc)[:120]}); "
              "the rebuild must parse them as vLLM does")

    shapes = conversations()
    probes = [(name, {**base, "messages": shapes[name]}) for name in ("tool turn", "follow-up")]
    smoke = [{"role": "user", "content": PROMPT}]
    probes += [("effort low (top-level field)", {**base, "messages": smoke, "reasoning_effort": "low"}),
               ("effort low (chat_template_kwargs)", {**base, "messages": smoke,
                                                       "chat_template_kwargs": {"reasoning_effort": "low"}}),
               ("effort medium (top-level field)", {**base, "messages": smoke, "reasoning_effort": "medium"}),
               ("follow-up, field `reasoning`", {**base, "messages": shapes["follow-up, field `reasoning`"]})]
    live = None if a.live == "all" else {int(x) for x in a.live.split(",")}
    for n, (name, body) in enumerate(probes, 1):
        if live is None or n in live:
            time.sleep(4)
            save(OUT / f"request-{n}.json", body, key)
            result = patient("POST", f"{BASE}/chat/completions", key, body)
            save(OUT / f"response-{n}.json", result["body"], key)
            how = f"HTTP {result['status']}, {result['seconds']} s"
        elif (OUT / f"response-{n}.json").exists():
            body = json.loads((OUT / f"request-{n}.json").read_text())
            result = {"body": json.loads((OUT / f"response-{n}.json").read_text()), "raw": ""}
            how = "from the saved request and response"
        else:
            continue
        server = (result["body"] or {}).get("prompt_token_ids")
        print(f"\n== {n}. {name}: {how}, "
              f"prompt_tokens {((result['body'] or {}).get('usage') or {}).get('prompt_tokens')}")
        if not server:
            print(f"   no prompt_token_ids; body starts {result['raw'][:300]!r}")
            continue
        if name.startswith("effort"):
            options = {"local effort=low": {"reasoning_effort": "low"},
                       "local effort=medium": {"reasoning_effort": "medium"}}
        else:
            options = {"local preserve_thinking unset": {"reasoning_effort": "medium"},
                       "local preserve_thinking=True": {"reasoning_effort": "medium", "preserve_thinking": True},
                       "local preserve_thinking=False": {"reasoning_effort": "medium", "preserve_thinking": False},
                       "local reasoning dropped, unset": {"reasoning_effort": "medium", "drop_reasoning": True}}
        for label, options_ in options.items():
            local = local_ids(tokenizer, body["messages"], **options_)
            print(f"   {label:32s} P {len(local):4d}: {verdict(tokenizer, server, local)}")


if __name__ == "__main__":
    main()
