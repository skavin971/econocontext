"""What a re-read costs, in the two cost modes of the [econo] line.

  gemini  dollars: re-read tokens x (input - cached price), Gemini's implicit-cache rules
          (the 4,096-token minimum, edit_quote.quoted_reachable_prefix) and the
          hidden-thinking meter (hidden.py). This is EconoCLM-Tools' existing behaviour.
  qwen    FLOPs for a locally served Qwen3.6-27B (vLLM prefix caching): no hidden-
          thinking meter, no 4,096 rule (vLLM caches in 16-token blocks). Every number
          comes from CLM's flops_metrics: N_body and the attention geometry for the model
          key, and its cost model (linear 2*N per token, plus 4*layers*width per causal
          attention pair).
"""

from dataclasses import dataclass

from clm_harness.flops_metrics import kv_cache_flops as kvf

from ..core import prices
from .edit_quote import quoted_reachable_prefix


@dataclass(frozen=True)
class Gemini:
    name: str = "gemini"

    def reachable(self, p: float) -> float:
        return quoted_reachable_prefix(p)

    def recompute_cost(self, start: float, end: float) -> float:
        """$ to re-read tokens start..end that were cached."""
        return max(0.0, end - start) * (prices.PRICE_IN - prices.PRICE_CACHED)

    def fmt_cost(self, x: float) -> str:
        return f"${x:.4f}" if x < 0.01 else f"${x:.3f}"


@dataclass(frozen=True)
class Qwen:
    model_key: str = "27b"
    name: str = "qwen"

    def reachable(self, p: float) -> float:
        block = kvf.DEFAULT_BLOCK_SIZE                       # vLLM's KV-cache block
        return (int(p) // block) * block

    def recompute_cost(self, start: float, end: float) -> float:
        """FLOPs to recompute (prefill) tokens start..end with tokens 0..start cached."""
        if end <= start:
            return 0.0
        n_body = kvf.resolve_n_body(self.model_key, required=True)
        geom = kvf.resolve_attn_geometry(self.model_key)
        flops = 2.0 * n_body * (end - start)
        if geom:
            n_layers, width = geom
            flops += 4.0 * n_layers * width * (end * end - start * start) / 2.0
        return flops

    def fmt_cost(self, x: float) -> str:
        if x >= 1e15:
            return f"{x / 1e15:.2f} PFLOPs"
        return f"{x / 1e12:.1f} TFLOPs"

    def call_flops(self, prompt: int, cached: int, output: int) -> float:
        """One call's FLOPs with CLM's model: prefill of the uncached tokens (linear +
        attention over the cached prefix), plus generation (linear + decode attention)."""
        n_body = kvf.resolve_n_body(self.model_key, required=True)
        geom = kvf.resolve_attn_geometry(self.model_key)
        flops = self.recompute_cost(cached, prompt) + 2.0 * n_body * output
        if geom:
            n_layers, width = geom
            flops += 4.0 * n_layers * width * (output * prompt + output * output / 2.0)
        return flops


def mode(name: str):
    if name == "gemini":
        return Gemini()
    if name == "qwen":
        return Qwen()
    raise ValueError(f"econo mode must be gemini or qwen, got {name!r}")


# ---------------------------------------------------------------- EconoCLM-Tools, qwen mode
def qwen_status_line(messages: list, *, cached: int | None, uncached: int | None, hits,
                     compute: float, n_stored: int, stale: list, protect: int, k: float,
                     count, cost: "Qwen") -> str:
    """The [econo] line of EconoCLM-Tools on Qwen (TEXT-TOOLS): tokens the last call
    reused from vLLM's cache and computed, compute so far, and what an edit at three
    depths would make the next call recompute (CLM's file flattening included)."""
    import os
    from .messages import first_structured, fmt_hits, fmt_tokens, positions
    from .status_line import DEPTHS
    import bisect
    parts = []
    if cached is None:
        parts.append("last call cache unknown")
    else:
        parts.append(f"last call {fmt_tokens(cached)} reused / "
                     f"{'?' if uncached is None else fmt_tokens(uncached)} computed")
    if fmt_hits(hits):
        parts.append(fmt_hits(hits))
    parts.append(f"compute so far {cost.fmt_cost(compute)}")
    if len(messages) <= protect:
        parts.append("edit: nothing editable yet")
    elif cached is None:
        parts.append("edit cost unknown")
    else:
        pos = [x * k for x in positions(messages, count)]
        start, end = pos[protect], pos[-1]
        struct = first_structured(messages, protect)
        seen, items = set(), []
        for f in DEPTHS:
            idx = max(protect, min(len(messages) - 1, bisect.bisect_right(pos, start + f * (end - start)) - 1))
            change = idx if struct is None else min(idx, struct)
            reach = cost.reachable(pos[change])
            tokens = max(0, cached - round(reach))
            if fmt_tokens(tokens) not in seen:
                seen.add(fmt_tokens(tokens))
                items.append((idx - protect + 1, tokens, cost.recompute_cost(reach, reach + tokens)))
        if len(items) == 1:
            parts.append(f"any edit now: ~{fmt_tokens(items[0][1])} recomputed ({cost.fmt_cost(items[0][2])})")
        else:
            parts.append(f"edit at turn ≤{items[0][0]}: ~{fmt_tokens(items[0][1])} recomputed "
                         f"({cost.fmt_cost(items[0][2])})"
                         + "".join(f", ≤{t}: ~{fmt_tokens(n)} ({cost.fmt_cost(c)})" for t, n, c in items[1:]))
    parts.append(f"stored: {n_stored}")
    if stale:
        names = [os.path.basename(p.rstrip("/")) or p for p in stale]
        more = f" +{len(names) - 3} more" if len(names) > 3 else ""
        parts.append("stale: " + ", ".join(names[:3]) + more)
    return "[econo] " + " | ".join(parts)


def qwen_quote(q, cached: int | None, hits, cost: "Qwen") -> None:
    """Rewrite an EditQuote for Qwen: R = cached tokens after the change (vLLM blocks,
    no 4,096 rule), in tokens and FLOPs. Changes q.R and q.line in place."""
    from .messages import fmt_hits, fmt_tokens
    q.below_min = False
    if cached is None:
        q.R = None
    else:
        reach = cost.reachable(q.prefix_tokens_p)
        q.R = max(0, cached - round(reach))
    delta = q.after_tokens - q.before_tokens
    sign = "−" if delta <= 0 else "+"
    parts = [f"[econo] edit: {fmt_tokens(q.before_tokens)}→{fmt_tokens(q.after_tokens)} tokens "
             f"({sign}{fmt_tokens(abs(delta))})"]
    if q.R is None:
        parts.append(f"first change at turn {q.turn}: cache unknown")
    else:
        reach = cost.reachable(q.prefix_tokens_p)
        parts.append(f"first change at turn {q.turn}: up to ~{fmt_tokens(q.R)} recomputed next call "
                     f"({cost.fmt_cost(cost.recompute_cost(reach, reach + q.R))})")
    if fmt_hits(hits):
        parts.append(fmt_hits(hits))
    if delta < 0:
        parts.append(f"every later prompt is ~{fmt_tokens(-delta)} tokens shorter")
    if q.removed:
        parts.append(f"removed: obs {', '.join(str(i) for i in q.removed)} (stored: econo get {q.removed[0]})")
    else:
        parts.append("removed: none")
    q.line = " | ".join(parts)
