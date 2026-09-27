"""Turn a provider price card into ratios relative to its own uncached input price.

Why it exists: internal costs are in normalized units (1 NU = one uncached input
token of the model in use), so the optimizer compares plans without caring which
provider or currency is behind them. Dollars appear only in reports.
What it must never do: hard-code a price or assume a cache discount exists.
"""

from datetime import date

from ..config import PriceCard, PriceTier
from ..types import RateRatios


def ratios(card: PriceCard, on: date, prompt_tokens: int | None = None) -> RateRatios:
    tier = card.tier(on, prompt_tokens)
    return from_tier(card.model, tier)


def from_tier(model: str, tier: PriceTier) -> RateRatios:
    base = tier.input_per_mtok
    if not base:
        raise ValueError(f"{model}: an input price is required to define 1 NU")

    def ratio(price: float | None) -> float:
        # An unverified cache price is treated as uncached input (ratio 1.0): no
        # discount is ever assumed that the price card does not state.
        return 1.0 if price is None else price / base

    return RateRatios(
        model=model,
        output_ratio=tier.output_per_mtok / base,
        cache_read_ratio=ratio(tier.cache_read_per_mtok),
        cache_write_ratio=ratio(tier.cache_write_per_mtok),
        cache_write_1h_ratio=(None if tier.cache_write_1h_per_mtok is None
                              else tier.cache_write_1h_per_mtok / base),
        usd_per_nu=base / 1_000_000,
    )
