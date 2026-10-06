"""Prices of keeping, shrinking, compacting and placing context, over the rest of a session.

Why it exists: every v2 rule compares options in one unit. The unit is "input-token
equivalents": 1 = one uncached input token. A token written to the cache costs `cache_write`,
one re-sent from the cache `cache_read`, one output token `output` (ratios from the price
card). `c` is what a resident token costs per later call: mostly a cache read, sometimes a
miss (hit_share). Append-only changes cost nothing extra; a compaction pays one cache write
of the new conversation. The formulas are the plan's; dollars = units × input $/token.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

CARD = Path(__file__).resolve().parents[2] / "config" / "billing_rates.yaml"


@dataclass(frozen=True)
class Prices:
    input_usd_per_mtok: float
    cache_read: float     # ratios to the input price
    cache_write: float
    output: float
    hit_share: float = 0.95
    expected_output: int = 500

    @property
    def c(self) -> float:
        """Cost of one resident token on one later call."""
        return self.hit_share * self.cache_read + (1 - self.hit_share)

    def usd(self, units: float) -> float:
        return units * self.input_usd_per_mtok / 1e6


def from_card(provider: str, model: str, hit_share: float = 0.95, expected_output: int = 500,
              card: Path = CARD, cache: bool = True) -> Prices:
    """Ratios from the current period's first tier in billing_rates.yaml.

    cache=False (v2.1, closed models): decisions do not plan around the provider's cache. Every
    kept token costs the full input price on every later call, writes cost the input price, and
    an edit breaks nothing that is priced. Any cache hit the provider gives is a bonus."""
    rates = yaml.safe_load(card.read_text())[provider][model]["periods"][0]["tiers"][0]
    base = rates["input_per_mtok"]
    if not cache:
        return Prices(base, 1.0, 1.0, rates["output_per_mtok"] / base, 0.0, expected_output)
    write = rates.get("cache_write_per_mtok", base * 1.25)
    return Prices(base, rates["cache_read_per_mtok"] / base, write / base,
                  rates["output_per_mtok"] / base, hit_share, expected_output)


def keep(p: Prices, tokens: float, calls_left: float) -> float:
    """Tokens appended now: written once, then re-sent on every later call."""
    return tokens * p.cache_write + tokens * p.c * calls_left


def reread(p: Prices, prompt: float, tokens: float, calls_left: float) -> float:
    """One extra call to get content back (the whole prompt re-sent, a short answer), then the
    content stays for the calls left."""
    return prompt * p.c + p.expected_output * p.output + keep(p, tokens, calls_left)


def arrival(p: Prices, full: int, shown: dict[str, int], miss: dict[str, float],
            calls_left: float, prompt: float, reread_at: float) -> dict[str, float]:
    """Price of each form a tool result can take now. `shown[form]` = tokens the model sees;
    `miss[form]` = chance the model needs something that form left out (it repeats the call);
    `reread_at` = calls from now until that re-read (soon for content in active use), after
    which the full content is carried for the rest."""
    out = {"full": keep(p, full, calls_left)}
    for form, tokens in shown.items():
        out[form] = (keep(p, tokens, calls_left)
                     + miss[form] * reread(p, prompt, full, max(0.0, calls_left - reread_at)))
    return out


def reread_at(lifetime: str | None, calls_left: float) -> float:
    """When a needed item would be fetched again, from the predictor's lifetime answer."""
    return {"few_calls": 1, "this_part": 3}.get(lifetime or "", calls_left / 2)


def note(p: Prices, note_tokens: int, full: int, calls_left: float) -> dict[str, float]:
    """A repeat answered with a short note instead of a second full copy."""
    return {"full": keep(p, full, calls_left), "note": keep(p, note_tokens, calls_left)}


def compaction(p: Prices, prompt: float, prefix: float, summary: float, calls_left: float,
               rereads: list[tuple[float, float]], summary_output: float | None = None) -> dict[str, float]:
    """Compact the conversation now (Claude Code /compact) or keep carrying it.

    Keeping: the conversation beyond the fixed prefix is re-sent on every later call.
    Compacting: one summary call (reads everything, writes `summary_output` tokens, thinking
    included; default `summary`), the `summary` kept is written to the cache once and carried
    after, plus each dropped item that is needed again
    (`rereads` = [(chance, tokens)]). Returns both prices and the break-even calls H*."""
    conversation = max(0.0, prompt - prefix)
    carry = conversation * p.c * calls_left
    summary_call = prompt * p.c + (summary if summary_output is None else summary_output) * p.output
    after = keep(p, summary, calls_left)
    risk = sum(chance * reread(p, prefix + summary, tokens, calls_left / 2) for chance, tokens in rereads)
    fixed = summary_call + summary * p.cache_write + risk
    saving_per_call = (conversation - summary) * p.c
    h_star = fixed / saving_per_call if saving_per_call > 0 else float("inf")
    return {"keep": carry, "compact": summary_call + after + risk, "h_star": h_star}


def placement(p: Prices, worker_history: float, follow_up_calls: float,
              fresh_first: float, rereads_saved: list[tuple[float, float]], prompt: float) -> dict[str, float]:
    """A follow-up sub-task: continue an idle worker (its history comes along on every call)
    or start a new one (it re-reads what it needs). `rereads_saved` = [(chance needed, tokens)]
    for what the idle worker already holds."""
    resume = worker_history * p.c * follow_up_calls
    fresh = fresh_first * p.cache_write + sum(
        chance * reread(p, prompt, tokens, follow_up_calls / 2) for chance, tokens in rereads_saved)
    return {"resume": resume, "fresh": fresh}
