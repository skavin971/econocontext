"""Gemini generateContent format -> EconoContext types. Translation only.

Why it exists: stock Gemini CLI calls the gateway in Gemini's own format (in Vertex
mode: /v1beta1/publishers/google/models/<model>:<method>). This is the single place
that reads that format: which call a path is, and what the provider reported.
It also reads what each call carried (observe_request, observe_response): sizes, hashes,
and the tool calls and results, never the prompt text.
What it must never do: decide anything, or change a request.

Usage mapping (checked against Vertex on 2026-09-30, docs/gemini-integration-baseline.md):
  usageMetadata.promptTokenCount          -> whole prompt, cached included
  usageMetadata.toolUsePromptTokenCount   -> prompt from tool use, billed as input
  usageMetadata.cachedContentTokenCount   -> cache_read (absent when nothing was cached: 0)
  usageMetadata.candidatesTokenCount      -> visible output
  usageMetadata.thoughtsTokenCount        -> reasoning, reported OUTSIDE candidates
                                             (7 + 1 + 91 = 99 = totalTokenCount), so it
                                             is added to billed output
  (no field)                              -> cache_write: implicit caching has no
                                             separately billed write
A missing output count is read as 0 only when totalTokenCount confirms it; otherwise
it is unknown (None), never zero.
"""

import hashlib
import json
import re

from econocontext.types import ProviderUsage

# ".../models/<model>:<method>", in both the Gemini API and Vertex path shapes.
MODEL_CALL = re.compile(r"/models/(?P<model>[^/:]+):(?P<method>\w+)$")
MODEL_METHODS = ("generateContent", "streamGenerateContent")  # billed model calls


def model_call(path: str) -> tuple[str | None, str | None]:
    """(model, method) for a path, e.g. ('gemini-3.6-flash', 'streamGenerateContent')."""
    match = MODEL_CALL.search(path.split("?", 1)[0])
    return (match["model"], match["method"]) if match else (None, None)


def extract_model(path: str) -> str | None:
    return model_call(path)[0]


def extract_usage(payloads: list[dict]) -> dict | None:
    """The reply's usageMetadata. A stream repeats it; the last complete one is the total."""
    for payload in reversed(payloads):
        usage = payload.get("usageMetadata") if isinstance(payload, dict) else None
        if usage and "promptTokenCount" in usage:
            return usage
    return None


def to_usage(usage: dict | None, latency_ms: float | None = None) -> ProviderUsage:
    if not usage or usage.get("promptTokenCount") is None:
        return ProviderUsage(None, None, None, None, latency_ms=latency_ms, raw=dict(usage or {}),
                             cache_write_applicable=False)
    prompt = usage["promptTokenCount"] + usage.get("toolUsePromptTokenCount", 0)
    cached = usage.get("cachedContentTokenCount", 0)
    candidates, thoughts = usage.get("candidatesTokenCount"), usage.get("thoughtsTokenCount")
    if prompt + (candidates or 0) + (thoughts or 0) == usage.get("totalTokenCount"):
        candidates, thoughts = candidates or 0, thoughts or 0  # absent means zero: the total says so
    output = None if candidates is None or thoughts is None else candidates + thoughts
    return ProviderUsage(uncached_input=prompt - cached, cache_read=cached, cache_write=None,
                         output=output, reasoning=thoughts, latency_ms=latency_ms,
                         raw=dict(usage), cache_write_applicable=False)


# Gemini CLI 0.62.0's own tools (seen in its requests, docs/omnigent-findings.md), by what
# they do to the workspace: (kind, argument naming the path). Kinds:
#   file    reads one file              search  reads the workspace (grep, glob, ls)
#   write   changes files ('*' = which ones is unknown: the shell)
#   meta    narration with no effect (update_topic)
#   other   everything else, including sub-agents (invoke_agent), web and planning.
# A tool missing here is 'other': never treated as a safe read.
TOOLS = {
    "read_file": ("file", "file_path"),
    "read_many_files": ("file", "paths"),
    "list_directory": ("search", "dir_path"),
    "glob": ("search", None),
    "grep_search": ("search", None),
    "search_file_content": ("search", None),
    "replace": ("write", "file_path"),
    "write_file": ("write", "file_path"),
    "run_shell_command": ("write", None),
    "update_topic": ("meta", None),
}


def tool_kind(name: str) -> str:
    return TOOLS.get(name, ("other", None))[0]


def tool_path(name: str, args: dict) -> str | None:
    """The file a tool call reads or writes, when its arguments name one."""
    key = TOOLS.get(name, ("other", None))[1]
    value = (args or {}).get(key) if key else None
    return value if isinstance(value, str) else None


def args_key(name: str, args) -> str:
    return hashlib.sha256(json.dumps([name, args], sort_keys=True, default=str).encode()).hexdigest()


def extract_contents(body: dict) -> list[dict]:
    contents = body.get("contents") if isinstance(body, dict) else None
    return [c for c in contents if isinstance(c, dict)] if isinstance(contents, list) else []


def _parts(content: dict) -> list[dict]:
    return [p for p in content.get("parts") or [] if isinstance(p, dict)]


def extract_function_calls(payloads: list[dict]) -> list[dict]:
    """The function calls in a reply (all stream chunks): [{name, args, id}]."""
    return [p["functionCall"] for payload in payloads if isinstance(payload, dict)
            for cand in payload.get("candidates") or [] for p in _parts(cand.get("content") or {})
            if isinstance(p.get("functionCall"), dict)]


def response_has_text(payloads: list[dict]) -> bool:
    """Whether a reply says anything besides function calls (thoughts do not count)."""
    return any(p.get("text", "").strip() and not p.get("thought")
               for payload in payloads if isinstance(payload, dict)
               for cand in payload.get("candidates") or [] for p in _parts(cand.get("content") or {}))


def extract_function_responses(body: dict) -> list[dict]:
    """Every function response in the request's history: [{name, id, response}]."""
    return [p["functionResponse"] for c in extract_contents(body) for p in _parts(c)
            if isinstance(p.get("functionResponse"), dict)]


def find_latest_tool_result(body: dict) -> list[dict]:
    """The tool results new in this request: the function responses after the last model
    turn, each paired with the call that asked for it (by id, else by position).
    Returns [{name, args, response}]."""
    contents = extract_contents(body)
    last_model = max((i for i, c in enumerate(contents) if c.get("role") == "model"), default=None)
    if last_model is None:
        return []
    calls = [p["functionCall"] for p in _parts(contents[last_model])
             if isinstance(p.get("functionCall"), dict)]
    by_id = {c.get("id"): c for c in calls if c.get("id")}
    results = []
    responses = [p["functionResponse"] for c in contents[last_model + 1:] for p in _parts(c)
                 if isinstance(p.get("functionResponse"), dict)]
    for i, r in enumerate(responses):
        call = by_id.get(r.get("id")) or (calls[i] if i < len(calls) else {})
        results.append({"name": r.get("name") or call.get("name"), "args": call.get("args") or {},
                        "response": r.get("response")})
    return results


def response_text(response) -> str:
    """The text a tool returned ({"output": ...} for Gemini CLI's own tools)."""
    if isinstance(response, dict) and isinstance(response.get("output"), str):
        return response["output"]
    return json.dumps(response, sort_keys=True, default=str)


def hash_request_prefix(body: dict) -> str:
    """Hash of what stays fixed from this call to the next: everything but the newest
    content. Equal prefixes on consecutive calls mean the history was only appended to."""
    fixed = {"systemInstruction": body.get("systemInstruction"), "tools": body.get("tools"),
             "contents": extract_contents(body)[:-1]}
    return hashlib.sha256(json.dumps(fixed, sort_keys=True).encode()).hexdigest()


def context_key(body: dict) -> str:
    """Which conversation a request belongs to: its system instruction and first user
    content. A sub-agent's requests have their own. It tells loops apart; it does not
    claim they are addressable agents."""
    system = body.get("systemInstruction") if isinstance(body, dict) else None
    contents = extract_contents(body)
    first = next((c for c in contents if c.get("role") == "user"), {})
    return hashlib.sha256(json.dumps([system, first], sort_keys=True).encode()).hexdigest()[:12]


def observe_request(body: dict, raw: bytes) -> dict:
    """What is recorded about a request (no prompt text): its size, shape and the tool
    results that entered it."""
    return {"request_hash": hashlib.sha256(raw).hexdigest(), "request_bytes": len(raw),
            "prefix_hash": hash_request_prefix(body), "contents": len(extract_contents(body)),
            "context_key": context_key(body),
            "function_responses": [{"name": r["name"], "args_key": args_key(r["name"], r["args"]),
                                    "bytes": len(response_text(r["response"]).encode())}
                                   for r in find_latest_tool_result(body)]}


def observe_response(payloads: list[dict]) -> dict:
    """What is recorded about a reply: the tool calls it asked for, and whether it said
    anything else."""
    return {"response_calls": [{"name": c.get("name"), "kind": tool_kind(c.get("name") or ""),
                                "args_key": args_key(c.get("name"), c.get("args") or {})}
                               for c in extract_function_calls(payloads)],
            "response_text": response_has_text(payloads)}
