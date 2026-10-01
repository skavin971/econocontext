"""The meter: what a call costs, and what a loop of calls will cost, in NU.

Why it exists: every price EconoContext predicts is "the meter applied to a forecast".
The meter is exact and deterministic; only the forecast (how many calls, how much each
adds) is uncertain. Keeping them apart is what makes a wrong prediction diagnosable:
wres4's placement was mispriced by its call count, not by its per-call cost.

  one call      c = F + w_w*W + w_1h*W1h + w_r*R + w_o*O          (price_call)
                F uncached input, W/W1h cache writes (5 min / 1 h), R cache reads, O output;
                the w's are the price card's ratios to uncached input (RateRatios).
                pricing/ledger.py prices every recorded call with this function.

  a loop        N calls of one conversation, append-only (price_loop). Call 1 sends
                `cached` tokens already in the provider's cache plus `first` new ones;
                each later call adds `growth` tokens (the previous output and tool results)
                and every call writes `output` tokens. Call k's prompt is
                P_k = cached + first + (k-1)*growth.

  explicit cache (Anthropic breakpoints on every turn, as Claude Code sets them)
                call 1 reads `cached` and writes `first`; call k reads P_(k-1) and writes
                `growth`. Summed:
                  R = cached + (N-1)*P_1 + growth*(N-1)(N-2)/2
                  W = first + (N-1)*growth
                A cold loop (nothing cached) is cached=0, first=everything.
  implicit cache (Gemini) a call reads the previous prompt minus a lag (the newest
                ~2K tokens are not cached yet), unless it misses the cache entirely (about
                one call in five, at random); the rest is uncached input; nothing is billed
                for writing. In expectation:
                  R = (1-miss)*max(0, reads_explicit - lag*(calls that read))
                  F = sum(P_k) - R
                Misses are random, so one run can differ from its expectation by far more
                than the meter's error; the model is judged over many runs.

The (N-1)(N-2)/2 term is why the call count matters twice: every extra call pays for
the whole history before it. N may be fractional (an expectation).
What it must never do: forecast. Sizes and N come from the caller.
"""

from dataclasses import dataclass, replace

from ..types import ProviderUsage, RateRatios


def price_call(u: ProviderUsage, r: RateRatios) -> float:
    """One call's cost in NU. Counters that were not reported count as 0 here; the
    ledger decides whether such a call's cost is complete."""
    write = (u.cache_write or 0) if u.cache_write_applicable else 0
    write_1h = (u.cache_write_1h or 0) if u.cache_write_applicable else 0
    w_1h = r.input_ratio if r.cache_write_1h_ratio is None else r.cache_write_1h_ratio
    return float((u.uncached_input or 0) * r.input_ratio + (u.cache_read or 0) * r.cache_read_ratio
                 + write * r.cache_write_ratio + write_1h * w_1h + (u.output or 0) * r.output_ratio)


@dataclass(frozen=True)
class Loop:
    """A loop of model calls to price (see the module doc)."""
    cached: float     # tokens already cached when call 1 is sent (a shared or warm prefix)
    first: float      # call 1's other prompt tokens (the task, a brief, a cold history)
    calls: float      # N
    growth: float     # tokens each later call adds
    output: float     # output tokens per call

    @property
    def reads(self) -> float:
        """Cached prefix tokens over the loop, under a cache that keeps every turn."""
        n, p1 = max(self.calls, 0.0), self.cached + self.first
        if n <= 0:
            return 0.0
        return self.cached + (n - 1) * p1 + self.growth * (n - 1) * (n - 2) / 2

    @property
    def prompt(self) -> float:
        """Prompt tokens over the loop: sum of P_k."""
        n, p1 = max(self.calls, 0.0), self.cached + self.first
        return n * p1 + self.growth * n * (n - 1) / 2


@dataclass(frozen=True)
class ExplicitCache:
    """Anthropic-style: the previous call's prompt is read, the new part written (5 min)."""

    def price(self, loop: Loop, r: RateRatios) -> float:
        n = max(loop.calls, 0.0)
        if n <= 0:
            return 0.0
        written = loop.first + (n - 1) * loop.growth
        return (loop.reads * r.cache_read_ratio + written * r.cache_write_ratio
                + n * loop.output * r.output_ratio)


@dataclass(frozen=True)
class ImplicitCache:
    """Gemini-style: the previous prompt minus `lag` is read, except on a miss
    (probability `miss`); no write price."""
    miss: float
    lag: float

    def read(self, previous_prompt: float) -> float:
        """Expected cached tokens of a call whose previous prompt was that long."""
        return (1 - self.miss) * max(0.0, previous_prompt - self.lag)

    def price(self, loop: Loop, r: RateRatios) -> float:
        n = max(loop.calls, 0.0)
        if n <= 0:
            return 0.0
        readers = max(n - 1, 0.0) + (1 if loop.cached else 0)
        read = (1 - self.miss) * max(0.0, loop.reads - self.lag * readers)
        return ((loop.prompt - read) * r.input_ratio + read * r.cache_read_ratio
                + n * loop.output * r.output_ratio)


def fit_implicit(pairs: list[tuple[int, int]]) -> ImplicitCache:
    """An implicit cache from recorded consecutive calls of one loop, each pair
    (previous prompt tokens, cache_read tokens): the share of calls that read nothing,
    and the median gap between the previous prompt and what was read."""
    hits = [p - r for p, r in pairs if r > 0]
    if not pairs or not hits:
        return ImplicitCache(miss=1.0, lag=0.0)
    hits.sort()
    return ImplicitCache(miss=1 - len(hits) / len(pairs), lag=float(hits[len(hits) // 2]))


def price_quantiles(loop: Loop, cache, r: RateRatios,
                    calls: tuple[float, float, float]) -> tuple[float, float, float]:
    """The loop priced at three call counts (the forecast's 10th, 50th, 90th percentile)."""
    return tuple(cache.price(replace(loop, calls=n), r) for n in calls)
