"""The two usage/price records EconoCLM shares between its components.

Copied (trimmed to these two) from econocontext/types.py on branch
feature/claude-code @ ae9fd5a. Standard library only.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderUsage:
    """Provider-neutral usage for one model call. None = not reported, never zero."""
    uncached_input: int | None
    cache_read: int | None
    cache_write: int | None
    output: int | None               # includes reasoning
    reasoning: int | None = None     # subset of output, for explanation only
    cache_write_1h: int | None = None  # Anthropic 1-hour writes, when reported
    latency_ms: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)  # original fields, for audit
    # False means the provider/cache mode has no separately billed write counter.
    # It is distinct from cache_write=None, which otherwise means "not reported".
    cache_write_applicable: bool = True

    @property
    def prompt_tokens(self) -> int | None:
        parts = (self.uncached_input, self.cache_read, self.cache_write, self.cache_write_1h)
        return None if self.uncached_input is None else sum(p or 0 for p in parts)


@dataclass
class RateRatios:
    """A price card as ratios to the model's uncached input price (input = 1.0)."""
    model: str
    output_ratio: float
    cache_read_ratio: float
    cache_write_ratio: float
    cache_write_1h_ratio: float | None
    usd_per_nu: float                # the uncached input price per token, in USD
    input_ratio: float = 1.0
