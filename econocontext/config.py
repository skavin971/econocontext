"""Load and validate config/econocontext.yaml and config/billing_rates.yaml.

Why it exists: no number that affects a decision may live in code (principle 7);
this module is the one place those numbers are read, checked and fingerprinted.
What it must never do: invent a default for a missing value. A missing required
key is an error, so a typo cannot silently become a different experiment.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .types import Constraints, Mode, Objective

REQUIRED = ("mode", "model", "objective", "constraints", "allowlist", "quality_risk", "planner",
            "cost_model", "predictor", "latency", "cache", "guard", "limits", "storage")


class ConfigError(ValueError):
    pass


@dataclass
class PriceTier:
    max_prompt_tokens: int | None     # None = no upper bound (or not yet verified)
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float | None
    cache_write_per_mtok: float | None
    cache_write_1h_per_mtok: float | None = None


@dataclass
class PriceCard:
    provider: str
    model: str
    source_url: str
    retrieved_on: str
    min_cacheable_tokens: int | None
    periods: list[dict[str, Any]]     # [{valid_from, valid_until, tiers: [PriceTier]}]

    def period_and_tier(self, on: date, prompt_tokens: int | None) -> tuple[dict[str, Any], PriceTier]:
        """Return the billing period and tier for a call on `on`."""
        for period in self.periods:
            start, end = period["valid_from"], period["valid_until"]
            if (start is None or on >= date.fromisoformat(start)) and (
                end is None or on <= date.fromisoformat(end)
            ):
                for tier in period["tiers"]:
                    limit = tier.max_prompt_tokens
                    if limit is None or prompt_tokens is None or prompt_tokens <= limit:
                        return period, tier
                return period, period["tiers"][-1]
        raise ConfigError(f"No price period for {self.model} on {on}")

    def tier(self, on: date, prompt_tokens: int | None) -> PriceTier:
        """The tier that applies to a call on `on` with this prompt size."""
        return self.period_and_tier(on, prompt_tokens)[1]


@dataclass
class Config:
    raw: dict[str, Any]
    card: PriceCard
    cards: dict[tuple[str, str], PriceCard]
    fingerprint: str

    @property
    def mode(self) -> Mode:
        return Mode(self.raw["mode"])

    @property
    def constraints(self) -> Constraints:
        c = self.raw["constraints"]
        return Constraints(objective=Objective(self.raw["objective"]),
                           max_cost_nu=c["max_cost_nu"], max_latency_ms=c["max_latency_ms"],
                           max_quality_risk=c["max_quality_risk"], latency_weight=c["latency_weight"])

    def section(self, name: str) -> dict[str, Any]:
        return self.raw[name]


def _cards(billing: dict[str, Any]) -> dict[tuple[str, str], PriceCard]:
    cards = {}
    for provider, models in billing.items():
        for model, spec in models.items():
            periods = [dict(p, tiers=[PriceTier(**t) for t in p["tiers"]]) for p in spec["periods"]]
            cards[(provider, model)] = PriceCard(
                provider=provider, model=model, source_url=spec["source_url"],
                retrieved_on=str(spec["retrieved_on"]),
                min_cacheable_tokens=spec.get("min_cacheable_tokens"), periods=periods)
    return cards


def load(config_dir: str | Path) -> Config:
    folder = Path(config_dir)
    raw = yaml.safe_load((folder / "econocontext.yaml").read_text())
    billing = yaml.safe_load((folder / "billing_rates.yaml").read_text())
    missing = [key for key in REQUIRED if key not in raw]
    if missing:
        raise ConfigError(f"econocontext.yaml is missing {missing}")
    Mode(raw["mode"])
    Objective(raw["objective"])
    if raw["objective"] == "balanced" and raw["constraints"]["latency_weight"] is None:
        raise ConfigError("objective 'balanced' needs constraints.latency_weight")
    cards = _cards(billing)
    key = (raw["model"]["provider"], raw["model"]["name"])
    if key not in cards:
        raise ConfigError(f"No price card for {key} in billing_rates.yaml")
    # The fingerprint covers both files, so a run can be matched to its exact settings.
    canonical = json.dumps({"config": raw, "billing": billing}, sort_keys=True, default=str)
    return Config(raw=raw, card=cards[key], cards=cards,
                  fingerprint=hashlib.sha256(canonical.encode()).hexdigest())
