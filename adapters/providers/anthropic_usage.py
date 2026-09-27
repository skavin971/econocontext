"""Anthropic usage -> ProviderUsage. STUB for later hosts; not used this phase.

Mapping (platform.claude.com prompt-caching docs, read 2026-09-27):
  usage.input_tokens                              -> uncached_input
      ("tokens after the last cache breakpoint", NOT the whole prompt)
  usage.cache_read_input_tokens                   -> cache_read
  usage.cache_creation.ephemeral_5m_input_tokens  -> cache_write
  usage.cache_creation.ephemeral_1h_input_tokens  -> cache_write_1h
      (fall back to usage.cache_creation_input_tokens as cache_write when the breakdown is absent)
  usage.output_tokens                             -> output
Whole prompt = input_tokens + cache_read_input_tokens + cache_creation_input_tokens.
"""

from econocontext.types import ProviderUsage


def to_provider_usage(usage: dict | None, latency_ms: float | None = None) -> ProviderUsage:
    # PLACEHOLDER: untested against live Anthropic responses; enable with the Anthropic host.
    if not usage:
        return ProviderUsage(None, None, None, None, latency_ms=latency_ms, raw={})
    breakdown = usage.get("cache_creation") or {}
    write_5m = breakdown.get("ephemeral_5m_input_tokens", usage.get("cache_creation_input_tokens"))
    return ProviderUsage(uncached_input=usage.get("input_tokens"),
                         cache_read=usage.get("cache_read_input_tokens"),
                         cache_write=write_5m, cache_write_1h=breakdown.get("ephemeral_1h_input_tokens"),
                         output=usage.get("output_tokens"), latency_ms=latency_ms, raw=dict(usage))
