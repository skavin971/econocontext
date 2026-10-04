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


def mode(name: str):
    if name == "gemini":
        return Gemini()
    if name == "qwen":
        return Qwen()
    raise ValueError(f"econo mode must be gemini or qwen, got {name!r}")
