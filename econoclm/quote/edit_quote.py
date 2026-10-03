"""The [econo] line shown after a context edit. Pure: no I/O.

  B  tokens of the whole message list before the edit; A after
  p  tokens of all messages before the first message that changed (system and
     task messages included)
  c  cached tokens reported for the last model call (the call made before this
     command; its prompt was exactly `before`)
  R = max(0, c - p)      cached tokens that now sit after the change: likely
                         re-read at full price next call ("~": Gemini's cache is
                         partly random)
  extra_now       = R * (price_in - price_cached)
  saving_per_call = max(0, B - A) * price_cached
  payoff_calls    = extra_now / saving_per_call   ("n/a" if the saving is 0)
  removed         = obs IDs tagged in `before` but not in `after`

B, A and p are counted with CLM's tokenizer and scaled by k (provider tokens per
our token, from the last call) so they are in the same units as c.
"""

from dataclasses import dataclass, field
from typing import Callable

from ..core import prices
from .messages import default_count, first_change, fmt_tokens, fmt_usd, obs_ids, positions


@dataclass
class EditQuote:
    before_tokens: int
    after_tokens: int
    first_change_msg: int
    turn: int                    # CLM's [[CTX_TURN n]] number of that message
    prefix_tokens_p: int
    cached_c: int | None
    R: int | None
    extra_usd: float | None
    saving_usd: float
    payoff_calls: float | None
    removed: list[int] = field(default_factory=list)
    calibration: float = 1.0
    line: str = ""


def edit_quote(before: list[dict], after: list[dict], cached_c: int | None, *,
               protect: int = 2, k: float = 1.0,
               count: Callable[[list[dict]], int] = default_count,
               price_in: float = prices.PRICE_IN,
               price_cached: float = prices.PRICE_CACHED) -> EditQuote | None:
    """The quote for the edit that turned `before` into `after`; None if nothing changed."""
    idx = first_change(before, after)
    if idx is None:
        return None
    pos_before = positions(before, count)
    B = round(pos_before[-1] * k)
    A = round(positions(after, count)[-1] * k)
    p = round(pos_before[idx] * k)
    removed = sorted(obs_ids(before) - obs_ids(after))
    saving = max(0, B - A) * price_cached

    if cached_c is None:
        R = extra = payoff = None
    else:
        R = max(0, cached_c - p)
        extra = R * (price_in - price_cached)
        payoff = extra / saving if saving > 0 else None

    q = EditQuote(before_tokens=B, after_tokens=A, first_change_msg=idx,
                  turn=idx - protect + 1, prefix_tokens_p=p, cached_c=cached_c, R=R,
                  extra_usd=extra, saving_usd=saving, payoff_calls=payoff, removed=removed,
                  calibration=k)
    q.line = render(q)
    return q


def render(q: EditQuote) -> str:
    delta = q.after_tokens - q.before_tokens
    sign = "−" if delta <= 0 else "+"
    parts = [f"[econo] edit: {fmt_tokens(q.before_tokens)}→{fmt_tokens(q.after_tokens)} "
             f"tokens ({sign}{fmt_tokens(abs(delta))})"]
    if q.R is None:
        parts.append(f"first change at turn {q.turn}: cache unknown")
    else:
        parts.append(f"first change at turn {q.turn}: ~{fmt_tokens(q.R)} re-read next call "
                     f"(~{fmt_usd(q.extra_usd)})")
    pays = "n/a" if q.payoff_calls is None else f"~{q.payoff_calls:.0f} calls"
    parts.append(f"saves ~{fmt_usd(q.saving_usd)} per later call → pays off after {pays}")
    if q.removed:
        ids = ", ".join(str(i) for i in q.removed)
        parts.append(f"removed: obs {ids} (stored: econo get {q.removed[0]})")
    else:
        parts.append("removed: none")
    return " | ".join(parts)
