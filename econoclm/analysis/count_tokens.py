"""Exact visible-token counts from Vertex's countTokens (free, no generation).

  python -m econoclm.analysis.count_tokens runs/<phase>     # writes visible_tokens.json

For every logged request body (bodies/<run_id>/<call_no>.request.json, gateway
--log-bodies), the OpenAI-style messages are converted to Gemini's native format WITHOUT
thought signatures and counted. Then, per call:

  visible = countTokens(request without signatures)
  hidden  = prompt_tokens billed - visible          (the hidden thinking Gemini read)

Checked on calls that cannot carry hidden thinking (a run's first call): visible must
equal the billed prompt_tokens there, or the conversion is off.

Secrets come from the gateway's secrets file (core/gateway.load_secrets); the key is
sent as the x-goog-api-key header only and never printed or saved.
"""

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..core.gateway import load_secrets

MODEL = "gemini-3.6-flash"


def endpoint(base_url: str, model: str = MODEL) -> str:
    """.../v1/projects/P/locations/L/endpoints/openapi -> the model's countTokens URL."""
    m = re.match(r"^(https://[^/]+/v1/projects/[^/]+/locations/[^/]+)/", base_url.rstrip("/") + "/")
    if not m:
        raise SystemExit("ECONOCONTEXT_BASE_URL is not a Vertex project/location URL")
    return f"{m.group(1)}/publishers/google/models/{model}:countTokens"


def _text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)


def to_native(body: dict) -> dict:
    """OpenAI chat request -> Gemini countTokens request, thought signatures dropped."""
    system, contents, names = [], [], {}
    for m in body["messages"]:
        role = m.get("role")
        if role == "system":
            system.append({"text": _text(m.get("content"))})
        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": _text(m.get("content"))}]})
        elif role == "assistant":
            parts = [{"text": _text(m.get("content"))}] if _text(m.get("content")) else []
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                try:
                    args = json.loads(args) if isinstance(args, str) else (args or {})
                except ValueError:
                    args = {"_raw": args}
                names[tc.get("id")] = fn.get("name")
                parts.append({"functionCall": {"name": fn.get("name"), "args": args}})
            contents.append({"role": "model", "parts": parts or [{"text": ""}]})
        elif role == "tool":
            part = {"functionResponse": {"name": names.get(m.get("tool_call_id"), "bash"),
                                         "response": {"content": _text(m.get("content"))}}}
            # consecutive tool results go in one user turn, as Gemini expects
            if contents and contents[-1]["role"] == "user" and "functionResponse" in contents[-1]["parts"][0]:
                contents[-1]["parts"].append(part)
            else:
                contents.append({"role": "user", "parts": [part]})
    req = {"contents": contents}
    if system:
        req["systemInstruction"] = {"parts": system}
    if body.get("tools"):
        req["tools"] = [{"functionDeclarations": [
            {k: v for k, v in (t.get("function") or {}).items() if k in ("name", "description", "parameters")}
            for t in body["tools"]]}]
    return req


def count(url: str, key: str, native: dict) -> int:
    req = urllib.request.Request(url, json.dumps(native).encode(),
                                 {"Content-Type": "application/json", "x-goog-api-key": key})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())["totalTokens"]
        except urllib.error.HTTPError as err:
            if err.code in (429, 503) and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise SystemExit(f"countTokens HTTP {err.code}: {err.read()[:300].decode(errors='replace')}")
    raise SystemExit("countTokens: gave up")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("day", type=Path)
    ap.add_argument("--limit", type=int, default=None, help="count only the first N bodies")
    args = ap.parse_args()
    secrets = load_secrets()
    url, key = endpoint(secrets["ECONOCONTEXT_BASE_URL"]), secrets["AGENT_PLATFORM_API_KEY"]
    out_path = args.day / "visible_tokens.json"
    out = json.loads(out_path.read_text()) if out_path.exists() else {}
    files = sorted((args.day / "bodies").glob("*/*.request.json"))[: args.limit]
    for f in files:
        k = f"{f.parent.name}/{f.name[:4]}"
        if k in out:
            continue
        out[k] = count(url, key, to_native(json.loads(f.read_text())))
        out_path.write_text(json.dumps(out, indent=0, sort_keys=True))
    print(f"{len(out)} requests counted -> {out_path}")


if __name__ == "__main__":
    main()
