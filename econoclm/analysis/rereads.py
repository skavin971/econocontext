"""Per call: how much uncached input came from re-reading old context, not new text.

For call k (k >= 1) with sent messages S_k and the ledger row U_k:

  rewrite        S_(k-1) is not a prefix of S_k (a context edit, or a rollback,
                 rewrote something already sent)
  appended       tokens newly added since call k-1: everything after S_(k-1) for an
                 append-only call; for a rewrite, the messages from the newest
                 structured assistant turn on (CLM appends the assistant turn and
                 its tool result after the rewritten, flattened list)
  extra_uncached = max(0, uncached_k - appended)    (provider tokens)
  extra_usd      = extra_uncached * (price_in - price_cached)

Our token counts (CLM's tokenizer) are scaled to the provider's units with
k = prompt_tokens_k / our count of S_k. Append-only calls also show some
extra_uncached (Gemini's implicit cache misses at random, about 1 call in 5): that
is reported apart, as the background.
"""

from typing import Callable

from ..core import prices
from ..quote.messages import default_count, first_change


def appended_start(prev: list[dict], cur: list[dict], rewrite: bool) -> int:
    if not rewrite:
        return len(prev)
    for i in range(len(cur) - 1, -1, -1):
        m = cur[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            return i
    return max(0, len(cur) - 2)


def call_rereads(snaps: list[list[dict]], calls: list[dict],
                 count: Callable[[list[dict]], int] | None = None) -> list[dict]:
    count = count or default_count
    out = []
    for i in range(1, min(len(snaps), len(calls))):
        prev, cur, row = snaps[i - 1], snaps[i], calls[i]
        j = first_change(prev, cur)
        rewrite = j is not None and j < len(prev)
        ours = count(cur)
        k = (row["prompt_tokens"] / ours) if (row["prompt_tokens"] and ours) else 1.0
        appended = round(count(cur[appended_start(prev, cur, rewrite):]) * k)
        uncached = row["uncached_tokens"] or 0
        extra = max(0, uncached - appended)
        out.append({"call": i, "rewrite": rewrite, "first_change": j, "appended": appended,
                    "uncached": uncached, "cached": row["cached_tokens"],
                    "extra_uncached": extra,
                    "extra_usd": extra * (prices.PRICE_IN - prices.PRICE_CACHED)})
    return out
