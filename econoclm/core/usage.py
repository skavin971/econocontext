"""Provider usage JSON -> token counts. Translation only.

The logic of to_usage is copied exactly from omnigent_layer/wire.py on branch
feature/claude-code @ ae9fd5a (checked against Vertex on 2026-09-28):

  usage.prompt_tokens                          -> whole prompt, cached included
  usage.prompt_tokens_details.cached_tokens    -> cache_read. The details are ABSENT when
                                                  nothing was cached, and Vertex then bills
                                                  no cache discount: recorded as 0 (raw
                                                  keeps the absence, for audit)
  (no field)                                   -> cache_write: Gemini's implicit cache has
                                                  no separately billed write
  usage.completion_tokens                      -> visible output
  usage.completion_tokens_details.reasoning_tokens -> reasoning. Vertex reports it OUTSIDE
      completion_tokens (prompt + completion + reasoning == total_tokens), so it is added
      to billed output. When the three do not add up to the total, reasoning is taken as
      already inside completion_tokens (the OpenAI convention) and not added twice.

`counts` is our small addition: the flat numbers the ledger and the quote use.
"""

from dataclasses import dataclass

from .types import ProviderUsage


def to_usage(usage: dict | None, latency_ms: float | None = None) -> ProviderUsage:
    if not usage:
        return ProviderUsage(None, None, None, None, latency_ms=latency_ms, raw={},
                             cache_write_applicable=False)
    prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    details = usage.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else 0
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    output = completion
    if None not in (prompt, completion, reasoning) and \
            prompt + completion + reasoning == usage.get("total_tokens"):
        output = completion + reasoning  # reasoning reported outside completion_tokens
    uncached = None if prompt is None else prompt - (cached or 0)
    return ProviderUsage(uncached_input=uncached, cache_read=cached, cache_write=None,
                         output=output, reasoning=reasoning, latency_ms=latency_ms,
                         raw=dict(usage), cache_write_applicable=False)


@dataclass
class Counts:
    """One call's tokens. None = not reported."""
    prompt: int | None      # whole prompt, cached included
    cached: int | None
    uncached: int | None
    output: int | None      # billed output, thinking included
    reasoning: int | None   # thinking only (subset of output)


def counts(usage: dict | None) -> Counts:
    u = to_usage(usage)
    return Counts(prompt=u.prompt_tokens, cached=u.cache_read, uncached=u.uncached_input,
                  output=u.output, reasoning=u.reasoning)
