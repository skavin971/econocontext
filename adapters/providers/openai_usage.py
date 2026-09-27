"""OpenAI usage -> ProviderUsage. STUB for later hosts; not used this phase.

Mapping (developers.openai.com prompt-caching guide, read 2026-09-27):
  usage.input_tokens                             -> whole prompt (includes cached)
  usage.input_tokens_details.cached_tokens       -> cache_read
  usage.input_tokens_details.cache_write_tokens  -> cache_write (GPT-5.6 and later only)
  usage.output_tokens                            -> output
  usage.output_tokens_details.reasoning_tokens   -> reasoning (subset of output)
"""

from econocontext.types import ProviderUsage


def to_provider_usage(usage: dict | None, latency_ms: float | None = None) -> ProviderUsage:
    # PLACEHOLDER: untested against live OpenAI responses; enable with an OpenAI host.
    if not usage:
        return ProviderUsage(None, None, None, None, latency_ms=latency_ms, raw={})
    details = usage.get("input_tokens_details") or {}
    total, cached = usage.get("input_tokens"), details.get("cached_tokens")
    write = details.get("cache_write_tokens")
    uncached = None if total is None or cached is None else total - cached - (write or 0)
    return ProviderUsage(uncached_input=uncached, cache_read=cached, cache_write=write,
                         output=usage.get("output_tokens"),
                         reasoning=(usage.get("output_tokens_details") or {}).get("reasoning_tokens"),
                         latency_ms=latency_ms, raw=dict(usage))
