"""The [econo] line shown after a context edit. Pure: no I/O.

  B  tokens of the whole message list before the edit; A after
  p  tokens before the first change: all messages before the first message that
     changed (system and task messages included), plus the text that message still
     shares with its new version (messages.shared_text)
  c  cached tokens reported for the last model call (the call made before this
     command; its prompt was exactly `before`)
  R = max(0, c - p')     with p' = p, or 0 if p < CACHE_MIN_TOKENS: Gemini's implicit
                         cache serves nothing when the unchanged prefix is shorter
                         than its minimum (4,096 tokens for Gemini 3.6 Flash; Gate 4:
                         0 cache hits in 18 rewrites with p below it). So R = the
                         cached tokens the edit puts out of reach: an UPPER
                         BOUND on what the edit makes the next call re-read (Gemini's
                         cache misses at random, so less may be re-read; the line
                         also shows the recent cache-hit rate)
  R_likely = max(0, min(c, A) - p')
                         only cached text that still exists after the edit can be
                         re-read (deleted text is not). Recorded, not shown: A, the
                         rebuilt plain text, is undercounted by our tokenizer by
                         ~5-25%, so R_likely is not a safe bound (Gate 4: exceeded
                         in 12 of 27 rewrites; R in 1, by 26 tokens)
  extra_now       = R * (price_in - price_cached)
  saving_per_call = max(0, B - A) * price_cached
  payoff_calls    = extra_now / saving_per_call   ("n/a" if the saving is 0)
  removed         = obs IDs tagged in `before` but not in `after`

B, A and p are what Gemini reads (hidden.py): the visible text counted with CLM's
tokenizer and scaled by k (provider tokens per our token for the visible part, from the
last call), plus the hidden thinking each signed tool-call turn carries in the state
Gemini was last measured in (hidden.py: none, live or all). The rebuild after an edit drops every signature, so A has none of the
editable turns' thinking: the saving includes it.
"""

from dataclasses import dataclass, field
from typing import Callable

from ..core import prices
from .hidden import hidden_per_message, provider_positions
from .messages import (default_count, first_change, fmt_hits, fmt_tokens, fmt_usd, obs_ids,
                       shared_text)

# Gemini 3.6 Flash's minimum for implicit caching (ai.google.dev caching docs, 2026-09-02).
CACHE_MIN_TOKENS = 4096


def reachable_prefix(p: float) -> float:
    """How much of an unchanged prefix of p tokens Gemini's cache can still serve."""
    return p if p >= CACHE_MIN_TOKENS else 0


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
    hidden_before: int = 0       # earlier thinking Gemini reads in `before`
    hidden_after: int = 0
    hits: tuple[int, int] | None = None   # (cache hits, calls) in the recent window
    R_likely: int | None = None
    below_min: bool = False      # the unchanged prefix is under CACHE_MIN_TOKENS
    line: str = ""


def edit_quote(before: list[dict], after: list[dict], cached_c: int | None, *,
               protect: int = 2, k: float = 1.0,
               count: Callable[[list[dict]], int] = default_count,
               thinking: dict[str, int] | None = None, mode: str = "live",
               hits: tuple[int, int] | None = None,
               price_in: float = prices.PRICE_IN,
               price_cached: float = prices.PRICE_CACHED) -> EditQuote | None:
    """The quote for the edit that turned `before` into `after`; None if nothing changed."""
    idx = first_change(before, after)
    if idx is None:
        return None
    pos_before = provider_positions(before, count, k, thinking, mode)
    B = round(pos_before[-1])
    A = round(provider_positions(after, count, k, thinking, mode)[-1])
    shared = (shared_text(before[idx], after[idx])
              if idx < len(before) and idx < len(after) else "")
    p = round(pos_before[idx] + (k * (count([{**after[idx], "content": shared}])
                                      - count([{**after[idx], "content": ""}])) if shared else 0))
    removed = sorted(obs_ids(before) - obs_ids(after))
    saving = max(0, B - A) * price_cached

    if cached_c is None:
        R = extra = payoff = None
    else:
        R = max(0, cached_c - reachable_prefix(p))
        extra = R * (price_in - price_cached)
        payoff = extra / saving if saving > 0 else None

    q = EditQuote(before_tokens=B, after_tokens=A, first_change_msg=idx,
                  turn=idx - protect + 1, prefix_tokens_p=p, cached_c=cached_c, R=R,
                  extra_usd=extra, saving_usd=saving, payoff_calls=payoff, removed=removed,
                  calibration=k, hidden_before=sum(hidden_per_message(before, thinking, mode)),
                  hidden_after=sum(hidden_per_message(after, thinking, mode)), hits=hits,
                  R_likely=None if cached_c is None else max(0, min(cached_c, A) - reachable_prefix(p)),
                  below_min=p < CACHE_MIN_TOKENS)
    q.line = render(q)
    return q


def render(q: EditQuote) -> str:
    delta = q.after_tokens - q.before_tokens
    sign = "−" if delta <= 0 else "+"
    parts = [f"[econo] edit: {fmt_tokens(q.before_tokens)}→{fmt_tokens(q.after_tokens)} "
             f"tokens ({sign}{fmt_tokens(abs(delta))})"
             + (f", earlier thinking {fmt_tokens(q.hidden_before)}→{fmt_tokens(q.hidden_after)}"
                if q.hidden_before or q.hidden_after else "")]
    if q.R is None:
        parts.append(f"first change at turn {q.turn}: cache unknown")
    else:
        parts.append(f"first change at turn {q.turn}: up to ~{fmt_tokens(q.R)} re-read next call "
                     f"({fmt_usd(q.extra_usd)})"
                     + (f"; unchanged prefix {fmt_tokens(q.prefix_tokens_p)} is below Gemini's "
                        f"{fmt_tokens(CACHE_MIN_TOKENS)} cache minimum, so none of it stays cached"
                        if q.below_min and q.R else ""))
    if fmt_hits(q.hits):
        parts.append(fmt_hits(q.hits))
    pays = "n/a" if q.payoff_calls is None else f"~{q.payoff_calls:.0f} calls"
    parts.append(f"saves ~{fmt_usd(q.saving_usd)} per later call → pays off after {pays}")
    if q.removed:
        ids = ", ".join(str(i) for i in q.removed)
        parts.append(f"removed: obs {ids} (stored: econo get {q.removed[0]})")
    else:
        parts.append("removed: none")
    return " | ".join(parts)
