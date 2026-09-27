"""Gemini / Vertex AI usage -> ProviderUsage, via LangChain's `usage_metadata`.

Why it exists: the core is provider-agnostic; this is the one place that knows how
Gemini's counters arrive and what they mean.
What it must never do: price anything or guess a missing counter.

Mapping (langchain-google-genai 4.4.0, chat_models.py, read 2026-09-27):
  input_tokens                     = prompt_token_count + tool_use_prompt_token_count
                                     (includes cached tokens)
  input_token_details.cache_read   = cached_content_token_count   (Gemini: cachedContentTokenCount)
  output_tokens                    = candidates_token_count + thoughts_token_count
  output_token_details.reasoning   = thoughts_token_count
Gemini implicit caching bills no separate cache write, so cache_write is None
(structurally absent, not unknown). Note: the integration writes cache_read = 0
when Gemini omits cachedContentTokenCount, so "no hit" and "not reported" are
indistinguishable at this layer; `raw` keeps the dict for audit. The Gemini-native
fields themselves are not exposed by the integration.
"""

from econocontext.types import ProviderUsage


def to_provider_usage(usage_metadata: dict | None, latency_ms: float | None = None) -> ProviderUsage:
    if not usage_metadata:
        return ProviderUsage(None, None, None, None, latency_ms=latency_ms, raw={})
    details = usage_metadata.get("input_token_details") or {}
    out_details = usage_metadata.get("output_token_details") or {}
    total_in = usage_metadata.get("input_tokens")
    cache_read = details.get("cache_read")
    uncached = None
    if total_in is not None and cache_read is not None:
        uncached = total_in - cache_read
        if uncached < 0:
            raise ValueError(f"cache_read exceeds input_tokens in {usage_metadata}")
    return ProviderUsage(
        uncached_input=uncached,
        cache_read=cache_read,
        cache_write=None,
        output=usage_metadata.get("output_tokens"),
        reasoning=out_details.get("reasoning", 0 if usage_metadata.get("output_tokens") is not None
                                  else None),
        latency_ms=latency_ms,
        raw=dict(usage_metadata),
    )
