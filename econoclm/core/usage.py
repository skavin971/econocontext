"""Provider usage JSON -> token counts. Translation only.

to_usage started as an exact copy of omnigent_layer/wire.py on branch
feature/claude-code @ ae9fd5a (checked against Vertex on 2026-09-28). It now differs
in how billed output is counted (see "Divergence" below):

  usage.prompt_tokens                          -> whole prompt, cached included
  usage.prompt_tokens_details.cached_tokens    -> cache_read. The details are ABSENT when
                                                  nothing was cached, and Vertex then bills
                                                  no cache discount: recorded as 0 (raw
                                                  keeps the absence, for audit)
  (no field)                                   -> cache_write: Gemini's implicit cache has
                                                  no separately billed write
  usage.completion_tokens                      -> visible output
  usage.completion_tokens_details.reasoning_tokens -> reasoning. Vertex reports it OUTSIDE
      completion_tokens (prompt + completion + reasoning == total_tokens).

Billed output (billed_output):
  - both present: output = completion + reasoning. If total_tokens is present and
    prompt + output != total, the call is an anomaly.
  - one missing, total and prompt present: output = total - prompt. The call is an
    anomaly if that is smaller than the component that is present.
  - no total: output = (completion or 0) + (reasoning or 0).
The gateway also marks a 200 reply with no usable output count as an anomaly
(ledger column usage_anomaly).

Divergence from the frozen code (2026-10-03, Gate 2): Vertex omits completion_tokens
when a reply has no visible text (thinking only, cut by max_tokens). The frozen logic
then recorded output as None and billed none of the thinking tokens; the smoke call
was charged for its 6 prompt tokens only. Frozen wire.py likely has the same gap; it
is left untouched. The frozen fallback for the OpenAI convention (reasoning already
inside completion_tokens) is gone: such a reply now shows up as an anomaly.

`counts` is our small addition: the flat numbers the ledger and the quote use.
"""

from dataclasses import dataclass

from .types import ProviderUsage


def billed_output(usage: dict | None) -> tuple[int | None, bool]:
    """(billed output tokens incl. thinking, whether the numbers don't add up)."""
    if not usage:
        return None, False
    prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    total = usage.get("total_tokens")
    if total is not None and prompt is not None:
        if completion is not None and reasoning is not None:
            output = completion + reasoning
            return output, prompt + output != total
        output = total - prompt  # a component is missing: the total says what was billed
        return output, output < (completion or 0) + (reasoning or 0)
    if completion is None and reasoning is None:
        return None, False
    return (completion or 0) + (reasoning or 0), False


def to_usage(usage: dict | None, latency_ms: float | None = None) -> ProviderUsage:
    if not usage:
        return ProviderUsage(None, None, None, None, latency_ms=latency_ms, raw={},
                             cache_write_applicable=False)
    prompt = usage.get("prompt_tokens")
    details = usage.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else 0
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    output, _ = billed_output(usage)
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
