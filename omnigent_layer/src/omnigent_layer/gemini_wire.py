"""Gemini generateContent format -> EconoContext types. Translation only.

Why it exists: stock Gemini CLI calls the gateway in Gemini's own format (in Vertex
mode: /v1beta1/publishers/google/models/<model>:<method>). This is the single place
that reads that format: which call a path is, and what the provider reported.
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
