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

Hidden thinking (quote/hidden.py): signed tool-call turns carry their call's thinking,
which Gemini reads as prompt tokens under the measured rule. Each call's reply is the
new assistant turn of the next snapshot, so its thinking is that call's
reasoning_tokens. Our counts of the visible text (CLM's tokenizer) are scaled to the
provider's units with k = (prompt_tokens_k - hidden_k) / our count of S_k, and
`appended` includes the hidden thinking of the appended turns. Append-only calls also
show some extra_uncached (Gemini's implicit cache misses at random, about 1 call in 5;
and Gemini drops earlier thinking when a user message is answered): that is reported
apart, as the background.

Provider-side retries (finish_reason in RETRY_REASONS) are left out, so call i lines
up with CLM's i-th model call and snapshot i.

Split of a rewrite's re-read (analysis only):
  j  first message whose WIRE form changed (first_change)
  e  first message whose TEXT changed: CLM's own rendered_text of the old message
     vs the text of the new one. CLM's parse_back turns every still-structured turn
     (tool_calls, tool results) into plain text on any edit, so turns j..e-1 changed
     format only; from e on, the model's edit changed the content.
  format re-read        = min(extra_uncached, tokens(S_(k-1)[j:e]) * k)
  edit-position re-read = extra_uncached - format re-read
The format part comes first because re-reading starts at j. Caveat: CLM's
_normalize merges consecutive same-role turns, so a merge counts as a text change
where it happens (it attributes slightly more to the edit position).
"""

from typing import Callable

from clm_harness.context_utils.context_string import rendered_text

from ..core import prices
from ..quote.hidden import hidden_per_message, sig_key, signature
from ..quote.messages import default_count, first_change
from .common import RETRY_REASONS


def first_text_change(prev: list[dict], cur: list[dict], start: int) -> int:
    """From `start`, the first index whose text (not format) differs; len(prev) if none."""
    for i in range(start, min(len(prev), len(cur))):
        if rendered_text(prev[i]).strip() != rendered_text(cur[i]).strip():
            return i
    return min(len(prev), len(cur))


def appended_start(prev: list[dict], cur: list[dict], rewrite: bool) -> int:
    if not rewrite:
        return len(prev)
    for i in range(len(cur) - 1, -1, -1):
        m = cur[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            return i
    return max(0, len(cur) - 2)


def thinking_from_snapshots(snaps: list[list[dict]], calls: list[dict]) -> dict[str, int]:
    """Signature key -> thinking tokens: call i's reply is a signed assistant turn that
    first appears in snapshot i+1."""
    seen: set[str] = set()
    out: dict[str, int] = {}
    for i, snap in enumerate(snaps):
        for m in snap:
            sig = signature(m) if m.get("role") == "assistant" else None
            if sig is None or sig_key(sig) in seen:
                continue
            seen.add(sig_key(sig))
            if 1 <= i <= len(calls):
                out[sig_key(sig)] = calls[i - 1]["reasoning_tokens"] or 0
    return out


def call_rereads(snaps: list[list[dict]], calls: list[dict],
                 count: Callable[[list[dict]], int] | None = None) -> list[dict]:
    count = count or default_count
    calls = [c for c in calls if c.get("finish_reason") not in RETRY_REASONS]
    thinking = thinking_from_snapshots(snaps, calls)
    out = []
    for i in range(1, min(len(snaps), len(calls))):
        prev, cur, row = snaps[i - 1], snaps[i], calls[i]
        j = first_change(prev, cur)
        rewrite = j is not None and j < len(prev)
        ours = count(cur)
        hidden = hidden_per_message(cur, thinking)
        h = sum(hidden)
        p = row["prompt_tokens"]
        k = ((p - h) / ours) if (p and ours and p > h) else 1.0
        a = appended_start(prev, cur, rewrite)
        appended = round(count(cur[a:]) * k) + sum(hidden[a:])
        uncached = row["uncached_tokens"] or 0
        extra = max(0, uncached - appended)
        fmt = 0
        e = None
        if rewrite:
            e = first_text_change(prev, cur, j)
            fmt = min(extra, round(count(prev[j:e]) * k))
        out.append({"call": i, "rewrite": rewrite, "first_change": j, "text_change": e,
                    "format_uncached": fmt, "edit_uncached": extra - fmt,
                    "format_usd": fmt * (prices.PRICE_IN - prices.PRICE_CACHED),
                    "edit_pos_usd": (extra - fmt) * (prices.PRICE_IN - prices.PRICE_CACHED),
                    "appended": appended, "hidden": h, "clm_count": ours,
                    "prompt": p, "k_visible": k,
                    "gap_s": (row["ts"] - calls[i - 1]["ts"]) if row.get("ts") is not None
                             and calls[i - 1].get("ts") is not None else None,
                    "uncached": uncached, "cached": row["cached_tokens"],
                    "extra_uncached": extra,
                    "extra_usd": extra * (prices.PRICE_IN - prices.PRICE_CACHED)})
    return out
