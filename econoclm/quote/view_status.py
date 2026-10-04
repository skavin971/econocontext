"""The [econo] line for EconoCLM-View. Pure: no I/O. Facts only; it never advises.

  [econo] prompt ~15.3K tokens (view: 24 lines) | last call 12.1K reused / 3.2K recomputed
  | cache hit on 7 of last 10 calls | Gemini read 15.3K, earlier thinking 2.0K | run $0.041
  | change at view line 6: up to ~9.8K recomputed ($0.0066), line 12: up to ~5.1K ($0.0034)
  | stored: 18 | stale: parser.py

"change at view line L" is the first line at 25%, 50% and 75% of the prompt (by token
position); the number is what a change there would make the next call recompute: the
reused (cached) tokens after that line's position, as an upper bound. Unlike CLM's file,
the view keeps every turn's structure, so a change at line L changes the prompt only from
that line on. Lines that give the same number are shown once.

Mode (costmode.py): gemini (dollars, Gemini's 4,096-token minimum, hidden thinking) or
qwen (FLOPs from CLM's flops_metrics, vLLM's 16-token blocks).
"""

import bisect
import os
from typing import Callable

from .hidden import provider_positions
from .messages import default_count, fmt_hits, fmt_tokens

DEPTHS = (0.25, 0.5, 0.75)


def view_depths(messages: list[dict], line_starts: list[int], cached: int | None, *,
                protect: int, k: float, count: Callable, thinking: dict | None, mode_name: str,
                cost) -> list[tuple[int, int, float]]:
    """(view line number, recompute tokens, cost) at the DEPTHS; [] if nothing editable."""
    if not line_starts or cached is None:
        return []
    pos = provider_positions(messages, count, k, thinking, mode_name)
    starts = [pos[protect + i] for i in line_starts]
    start, end = pos[protect], pos[-1]
    out = []
    for f in DEPTHS:
        target = start + f * (end - start)
        j = max(0, min(len(starts) - 1, bisect.bisect_right(starts, target) - 1))
        reach = cost.reachable(starts[j])
        tokens = max(0, cached - round(reach))
        out.append((j + 1, tokens, cost.recompute_cost(reach, reach + tokens)))
    return out


def view_status_line(messages: list[dict], line_starts: list[int], *, cached: int | None,
                     uncached: int | None, prompt_tokens: int | None, hits, read, run_cost: float,
                     n_stored: int, stale: list[str], protect: int, k: float,
                     thinking: dict | None, mode_name: str, cost,
                     count: Callable = default_count) -> str:
    parts = [f"prompt ~{fmt_tokens(prompt_tokens)} tokens" if prompt_tokens else "prompt size unknown"]
    parts[0] += f" (view: {len(line_starts)} lines)"
    if cached is None:
        parts.append("last call cache unknown")
    else:
        new = "?" if uncached is None else fmt_tokens(uncached)
        parts.append(f"last call {fmt_tokens(cached)} reused / {new} recomputed")
    if fmt_hits(hits):
        parts.append(fmt_hits(hits))
    if read is not None and cost.name == "gemini":
        gemini, _clm, hidden = read
        parts.append(f"Gemini read {fmt_tokens(gemini)}, earlier thinking {fmt_tokens(hidden)}")
    parts.append(f"run {cost.fmt_cost(run_cost)}")
    depths = view_depths(messages, line_starts, cached, protect=protect, k=k, count=count,
                         thinking=thinking, mode_name=mode_name, cost=cost)
    if not line_starts:
        parts.append("view is empty")
    elif not depths:
        parts.append("change cost unknown")
    else:
        seen, items = set(), []
        for line, tokens, c in depths:
            if fmt_tokens(tokens) in seen:
                continue
            seen.add(fmt_tokens(tokens))
            items.append((line, tokens, c))
        if len(items) == 1:
            _, tokens, c = items[0]
            parts.append(f"any change now: up to ~{fmt_tokens(tokens)} recomputed ({cost.fmt_cost(c)})")
        else:
            first, rest = items[0], items[1:]
            parts.append(f"change at view line {first[0]}: up to ~{fmt_tokens(first[1])} recomputed "
                         f"({cost.fmt_cost(first[2])})"
                         + "".join(f", line {ln}: up to ~{fmt_tokens(t)} ({cost.fmt_cost(c)})"
                                   for ln, t, c in rest))
    parts.append(f"stored: {n_stored}")
    if stale:
        names = [os.path.basename(p.rstrip("/")) or p for p in stale]
        more = f" +{len(names) - 3} more" if len(names) > 3 else ""
        parts.append("stale: " + ", ".join(names[:3]) + more)
    return "[econo] " + " | ".join(parts)
