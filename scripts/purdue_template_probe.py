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
`reasoning`, to see whether the server reads it there. Probes 7 and 8 (step 10b) send thinking on
through chat_template_kwargs.enable_thinking, with top_p 0.95, as the gateway now pins them. Probe 7 uses
the smoke prompt; probe 8 uses the tool turn with its reasoning removed, as the gateway removes it. Each
is judged with enable_thinking unset, true and false. Probe 9 is the control: thinking off and max_tokens 8.
If both fields reach the server, its prompt ends in an empty think block and the reply stops at 8 tokens.
Requests are spaced under the 20-per-minute limit.
The key is read from .env, never printed, and redacted from saved files.

It is the one script besides gateway/ that calls a model provider directly. Its helpers (ROOT to
save) are copied verbatim from the step-1 smoke test, scripts/purdue_smoke.py, removed in step 10
(at tag tier-a-step9).

Run (needs transformers): .venv-clm/bin/python scripts/purdue_template_probe.py [--live all|1,3,...]
  --live picks the probes to send; the others are judged again from their saved request and response.
"""

import argparse

import copy
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from transformers import AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://genai.rcac.purdue.edu/api"
MODEL = "qwen3.8:27b"
PROMPT = "List the files in the current directory. Use one of your tools to do it."
# Our agent's 4 tools, copied from agents/react/agent.py (TOOLS) on 2026-10-10.
TOOLS = [
    {"type": "function", "function": {
        "name": "bash", "description": "Run a bash command in the container; returns its output and exit code.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read lines of a text file, with line numbers (default: lines 1-400).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file", "description": "Write a whole text file (creates directories; replaces the file).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                       "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "submit", "description": "Finish: call this once the task is fully done.",
        "parameters": {"type": "object", "properties": {}}}},
]


def read_key(env: Path) -> str:
    for line in env.read_text().splitlines():
        if line.startswith("GENAI_API_KEY="):
            key = line.split("=", 1)[1].strip().strip('"').strip("'")
            if key:
                return key
    raise SystemExit("GENAI_API_KEY is not set in .env")


def request(method: str, url: str, key: str, body: dict | None = None) -> dict:
    """One request. Returns status, the parsed body (None if not JSON, or a JSON null), raw text, seconds."""
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, data=data, method=method,
                  headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urlopen(req, timeout=600) as response:
            status, raw = response.status, response.read().decode()
    except HTTPError as exc:
        status, raw = exc.code, exc.read().decode(errors="replace")
    except (URLError, TimeoutError) as exc:
        status, raw = None, f"{type(exc).__name__}: {exc}"
    seconds = round(time.monotonic() - started, 2)
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    return {"status": status, "body": parsed, "raw": raw, "seconds": seconds}


def patient(method: str, url: str, key: str, body: dict | None = None) -> dict:
    """The documented over-limit reply is a JSON null: wait and retry (twice at most), never flood."""
    for wait in (0, 20, 40):
        time.sleep(wait)
        result = request(method, url, key, body)
        result["waited_s"] = wait
        if result["body"] is not None and result["status"] == 200:
            return result
        print(f"  status {result['status']}, body {result['raw'][:200]!r}; waiting before a retry")
    return result


def save(path: Path, data, key: str) -> None:
    path.write_text(json.dumps(data, indent=1).replace(key, "[REDACTED]") + "\n")


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
    thinking = {"reasoning_effort": "medium", "top_p": 0.95, "chat_template_kwargs": {"enable_thinking": True}}
    without_reasoning = [{k: v for k, v in m.items() if m["role"] != "assistant" or k not in
                          ("reasoning", "reasoning_content", "provider_specific_fields")} for m in shapes["tool turn"]]
    probes += [("thinking on (chat_template_kwargs), smoke prompt", {**base, "messages": smoke, **thinking}),
               ("thinking on (chat_template_kwargs), tool turn without reasoning",
                {**base, "messages": without_reasoning, **thinking}),
               ("thinking off and max_tokens 8 (controls), smoke prompt",
                {**base, "messages": smoke, **thinking, "max_tokens": 8, "chat_template_kwargs": {"enable_thinking": False}})]
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
        usage, choice = (result["body"] or {}).get("usage") or {}, ((result["body"] or {}).get("choices") or [{}])[0]
        print(f"\n== {n}. {name}: {how}, prompt_tokens {usage.get('prompt_tokens')}, completion_tokens "
              f"{usage.get('completion_tokens')} (max_tokens {body.get('max_tokens')}), finish_reason {choice.get('finish_reason')}")
        if not server:
            print(f"   no prompt_token_ids; body starts {result['raw'][:300]!r}")
            continue
        if name.startswith("effort"):
            options = {"local effort=low": {"reasoning_effort": "low"},
                       "local effort=medium": {"reasoning_effort": "medium"}}
        elif name.startswith("thinking"):
            options = {"local enable_thinking unset": {"reasoning_effort": "medium"},
                       "local enable_thinking=True": {"reasoning_effort": "medium", "enable_thinking": True},
                       "local enable_thinking=False": {"reasoning_effort": "medium", "enable_thinking": False}}
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
