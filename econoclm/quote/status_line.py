"""The [econo] line shown after every command. Pure: no I/O.

It reports, without repeating CLM's own context size readout:
  - the last call's cached vs new (uncached) input tokens
  - information only: how many tokens Gemini read in the last call vs CLM's count of the
    same messages, and how much of it was earlier hidden thinking (hidden.py)
  - the run's cost so far
  - the price of an edit at three depths: at 25%, 50% and 75% of the editable
    region by token position. For each depth: the turn there, and
        re-read = max(0, c - change position)   and its extra cost
                  re-read * (price_in - price_cached)
    an UPPER BOUND (Gemini's cache misses at random). Depths that give the same number
    are shown once; if all do: "any edit now: up to ~N".
  - the recent cache-hit rate ("cache hit on 7 of last 10 calls")
    where the change position is min(that turn, the first turn CLM would rewrite
    anyway), see messages.py. Positions are what Gemini reads, hidden thinking included.
  - how many outputs are stored
  - stale files (at most 3 names, then "+k more")

Example:
[econo] last call 14.2K cached / 1.1K new | cache hit on 7 of last 10 calls | Gemini read 15.3K, CLM counts 6.0K, earlier thinking 9.1K | run $0.021 | edit at turn ≤2: up to ~0.3K re-read ($0.0002), ≤5: up to ~9K ($0.0061), ≤8: up to ~15K ($0.010) | stored: 3 | stale: parser.py
"""

import bisect
import os
from dataclasses import dataclass, field
from typing import Callable

from ..core import prices
from .hidden import provider_positions
from .messages import default_count, first_structured, fmt_hits, fmt_tokens, fmt_usd

DEPTHS = (0.25, 0.5, 0.75)


@dataclass
class Depth:
    fraction: float
    turn: int
    reread: int | None
    cost_usd: float | None


@dataclass
class Status:
    depths: list[Depth] = field(default_factory=list)
    line: str = ""


def edit_depths(messages: list[dict], cached_c: int | None, *, protect: int = 2,
                k: float = 1.0, count: Callable[[list[dict]], int] = default_count,
                thinking: dict[str, int] | None = None, mode: str = "live",
                price_in: float = prices.PRICE_IN,
                price_cached: float = prices.PRICE_CACHED) -> list[Depth]:
    if len(messages) <= protect:
        return []
    pos = provider_positions(messages, count, k, thinking, mode)
    start, end = pos[protect], pos[-1]
    struct = first_structured(messages, protect)
    out = []
    for f in DEPTHS:
        target = start + f * (end - start)
        # The editable message that contains the target position.
        idx = max(protect, min(len(messages) - 1, bisect.bisect_right(pos, target) - 1))
        change = idx if struct is None else min(idx, struct)
        if cached_c is None:
            reread = cost = None
        else:
            reread = max(0, cached_c - round(pos[change]))
            cost = reread * (price_in - price_cached)
        out.append(Depth(fraction=f, turn=idx - protect + 1, reread=reread, cost_usd=cost))
    return out


def depth_text(depths: list[Depth]) -> str:
    """'edit at turn ≤2: up to ~1K re-read ($x), ≤5: up to ~9K ($y)', one entry per distinct
    number (the shallowest turn that gives it); 'any edit now: up to ~N re-read ($x)' if
    every depth gives the same number."""
    distinct: dict[str, Depth] = {}
    for d in depths:
        distinct.setdefault(fmt_tokens(d.reread), d)
    if len(distinct) == 1:
        d = depths[0]
        return f"any edit now: up to ~{fmt_tokens(d.reread)} re-read ({fmt_usd(d.cost_usd)})"
    items = list(distinct.values())
    first, rest = items[0], items[1:]
    return (f"edit at turn ≤{first.turn}: up to ~{fmt_tokens(first.reread)} re-read "
            f"({fmt_usd(first.cost_usd)})"
            + "".join(f", ≤{d.turn}: up to ~{fmt_tokens(d.reread)} ({fmt_usd(d.cost_usd)})"
                      for d in rest))


def status_line(messages: list[dict], *, cached_c: int | None, uncached: int | None,
                run_cost_usd: float, n_stored: int, stale: list[str], protect: int = 2,
                k: float = 1.0, count: Callable[[list[dict]], int] = default_count,
                thinking: dict[str, int] | None = None, mode: str = "live",
                read: tuple[int, int, int] | None = None,
                hits: tuple[int, int] | None = None,
                price_in: float = prices.PRICE_IN,
                price_cached: float = prices.PRICE_CACHED) -> Status:
    """`read` = (tokens Gemini read in the last call, CLM's count of those messages,
    earlier hidden thinking in them): shown as information only."""
    depths = edit_depths(messages, cached_c, protect=protect, k=k, count=count,
                         thinking=thinking, mode=mode, price_in=price_in,
                         price_cached=price_cached)
    parts = []
    if cached_c is None:
        parts.append("last call cache unknown")
    else:
        new = "?" if uncached is None else fmt_tokens(uncached)
        parts.append(f"last call {fmt_tokens(cached_c)} cached / {new} new")
    if fmt_hits(hits):
        parts.append(fmt_hits(hits))
    if read is not None:
        gemini, clm, hidden = read
        parts.append(f"Gemini read {fmt_tokens(gemini)}, CLM counts {fmt_tokens(clm)}, "
                     f"earlier thinking {fmt_tokens(hidden)}")
    parts.append(f"run {fmt_usd(run_cost_usd)}")
    if not depths:
        parts.append("edit: nothing editable yet")
    elif cached_c is None:
        turns = ", ".join(dict.fromkeys(f"≤{d.turn}" for d in depths))
        parts.append(f"edit at turn {turns}: cache unknown")
    else:
        parts.append(depth_text(depths))
    parts.append(f"stored: {n_stored}")
    if stale:
        names = [os.path.basename(p.rstrip("/")) or p for p in stale]
        more = f" +{len(names) - 3} more" if len(names) > 3 else ""
        parts.append("stale: " + ", ".join(names[:3]) + more)
    return Status(depths=depths, line="[econo] " + " | ".join(parts))
