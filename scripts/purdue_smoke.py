"""Purdue GenAI Studio smoke test: what the API serves and returns, before any gateway exists.

Why it exists: step 1 of the Purdue/Qwen3.8 plan. The FLOPs measurement depends on facts only
real responses can give: which models are served (and which Hugging Face model `qwen3.8:27b`
is), the exact usage fields (a cached count? token ids?), where reasoning text comes back,
whether tool calls are well-formed, and the latency. It makes three requests (models, then the
same chat call twice), well under the 20-per-minute limit.

It is the one script besides gateway/ that calls a model provider directly (it predates the
gateway). It reads GENAI_API_KEY from .env, never prints it, and redacts it from every file it
saves.

Run: .venv/bin/python scripts/purdue_smoke.py [--out runs/purdue-smoke]
     .venv/bin/python scripts/purdue_smoke.py --ids-probe 4   (only: the same chat call N times with
     vLLM's `return_token_ids`, to see whether the server returns its exact prompt token ids)
"""

import argparse
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://genai.rcac.purdue.edu/api"
MODEL = "qwen3.8:27b"
PROMPT = "List the files in the current directory. Use one of your tools to do it."
# Our agent's 4 tools, copied from adapters/harbor/econo_agent.py (TOOLS) on 2026-10-10.
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


def describe_chat(label: str, result: dict) -> dict:
    body = result["body"] or {}
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    usage = body.get("usage")
    content = message.get("content") or ""
    names = {t["function"]["name"]: t["function"]["parameters"].get("required", []) for t in TOOLS}
    print(f"\n== {label}: HTTP {result['status']}, {result['seconds']} s")
    print(f"  top-level keys: {sorted(body)}")
    print(f"  usage (exact): {json.dumps(usage)}")
    details = (usage or {}).get("prompt_tokens_details")
    print(f"  prompt_tokens_details: {json.dumps(details)}; cached_tokens present: "
          f"{isinstance(details, dict) and 'cached_tokens' in details}")
    ids = sorted({k for k in body if "token_ids" in k or "logprobs" in k}
                 | {k for k in choice if "token_ids" in k or "logprobs" in k})
    print(f"  token ids / logprobs fields: {ids or 'none'}")
    print(f"  finish_reason: {choice.get('finish_reason')}; message keys: {sorted(message)}")
    reasoning = {k: len(message[k]) for k in ("reasoning_content", "reasoning") if isinstance(message.get(k), str)}
    print(f"  reasoning fields (chars): {reasoning or 'none'}; '<think>' in content: {'<think>' in content}; "
          f"content chars: {len(content)}")
    calls = message.get("tool_calls") or []
    for call in calls:
        fn = call.get("function") or {}
        try:
            args, parsed = json.loads(fn.get("arguments") or ""), True
        except ValueError:
            args, parsed = None, False
        ok = (isinstance(call.get("id"), str) and call.get("type") == "function" and fn.get("name") in names
              and parsed and isinstance(args, dict) and all(r in args for r in names.get(fn.get("name"), [])))
        print(f"  tool call: id={call.get('id')!r} type={call.get('type')!r} name={fn.get('name')!r} "
              f"arguments={fn.get('arguments')!r} -> well-formed: {ok}")
    if not calls:
        print(f"  no tool calls; content starts: {content[:300]!r}")
    return {"usage": usage, "content": content, "tool_calls": calls, "seconds": result["seconds"]}


def ids_probe(out: Path, key: str, n: int) -> None:
    """The smoke chat call n times with `return_token_ids` (a vLLM option the front end may drop).
    If ids come back, show where the prompts of identical requests differ."""
    chat = {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}], "tools": TOOLS,
            "max_tokens": 8192, "temperature": 0.7, "stream": False, "return_token_ids": True}
    prompts = []
    for i in range(1, n + 1):
        time.sleep(4)  # about 15 requests a minute at most, under the 20-per-minute limit
        result = patient("POST", f"{BASE}/chat/completions", key, chat)
        save(out / f"response-ids-{i}.json", result["body"], key)
        body = result["body"] or {}
        ids = body.get("prompt_token_ids") or (body.get("choices") or [{}])[0].get("prompt_token_ids")
        usage = body.get("usage") or {}
        print(f"  call {i}: HTTP {result['status']}, {result['seconds']} s, prompt_tokens {usage.get('prompt_tokens')}, "
              f"prompt_token_ids returned: {ids is not None}" + (f" ({len(ids)} ids)" if ids else ""))
        if ids:
            prompts.append(ids)
    for i, ids in enumerate(prompts[1:], 2):
        first = prompts[0]
        at = next((k for k, (x, y) in enumerate(zip(first, ids)) if x != y), min(len(first), len(ids)))
        print(f"  prompt 1 vs prompt {i}: lengths {len(first)}/{len(ids)}, first difference at token {at}: "
              f"{first[at:at + 8]} vs {ids[at:at + 8]}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", default=str(ROOT / "runs" / "purdue-smoke"))
    p.add_argument("--ids-probe", type=int, default=0, help="only run the token-id probe, this many calls")
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    key = read_key(ROOT / ".env")
    if a.ids_probe:
        print(f"== token-id probe: the smoke chat call {a.ids_probe} times with return_token_ids")
        return ids_probe(out, key, a.ids_probe)

    print("== GET /api/models")
    models = patient("GET", f"{BASE}/models", key)
    entries = (models["body"] or {}).get("data") or []
    ours = next((m for m in entries if m.get("id") == MODEL), {})
    capabilities = ((ours.get("info") or {}).get("meta") or {}).get("capabilities")
    # Saved and printed: the model ids and our model's capabilities only. The full list also holds
    # Purdue's internal user and access-grant ids, which must not be published.
    save(out / "models.json", {"source": f"GET {BASE}/models, trimmed to the ids and {MODEL}'s capabilities",
                               "ids": [m.get("id") for m in entries], MODEL: {"capabilities": capabilities}}, key)
    print(f"  HTTP {models['status']}, {models['seconds']} s, {len(entries)} models")
    print(f"  ids: {[m.get('id') for m in entries]}")
    if ours:
        text = json.dumps(ours)
        print(f"  {MODEL} keys: {sorted(ours)}")
        print(f"  {MODEL} capabilities: {json.dumps(capabilities)}")
        hits = sorted({w.strip('",') for w in text.replace("\\/", "/").split() if "Qwen/" in w or "huggingface" in w})
        print(f"  strings naming a Hugging Face model: {hits or 'none'}")
    newer = [m.get("id") for m in entries if any(v in json.dumps(m).lower() for v in ("qwen3.6", "qwen3.5", "qwen3_5"))]
    print(f"  models mentioning Qwen3.6 / Qwen3.5: {newer or 'none'}")

    chat = {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}], "tools": TOOLS,
            "max_tokens": 8192, "temperature": 0.7, "stream": False}
    save(out / "request.json", chat, key)
    runs = []
    for n in (1, 2):
        result = patient("POST", f"{BASE}/chat/completions", key, chat)
        save(out / ("response.json" if n == 1 else "response-2.json"), result["body"], key)
        if result["body"] is None:
            (out / f"response-{n}.raw.txt").write_text(result["raw"].replace(key, "[REDACTED]"))
        runs.append(describe_chat(f"chat call {n}", result))
        (out / f"latency-{n}.json").write_text(json.dumps({"seconds": result["seconds"],
                                                           "waited_s": result["waited_s"]}) + "\n")
    first, second = runs
    print("\n== second identical call vs the first")
    print(f"  usage equal: {first['usage'] == second['usage']} ({first['usage']} vs {second['usage']})")
    print(f"  same content: {first['content'] == second['content']}; same tool calls (ignoring ids): "
          f"{[c.get('function') for c in first['tool_calls']] == [c.get('function') for c in second['tool_calls']]}")
    print(f"  latency: {first['seconds']} s, then {second['seconds']} s")


if __name__ == "__main__":
    main()
