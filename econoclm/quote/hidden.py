"""Gemini's hidden thinking: tokens Gemini reads in a request that CLM's count does not see.

Measured on Vertex (Gemini 3.6 Flash), 2026-10-03 (runs/2026-10-03-g3-pilot-raw-8192,
runs/2026-10-03-replay/findings.md):

  - An assistant turn with tool calls carries its call's thinking as an opaque
    tool_calls[].extra_content.google.thought_signature. Gemini counts that thinking as
    prompt tokens on later calls: prompt(t) - prompt(t-1) = reasoning(t-1) + visible
    tokens added, within a few dozen tokens.
  - The thinking accumulates within an open run of tool calls and resets when a user
    message is answered: only tool-call turns after the most recent user message that
    already has an assistant reply carry it. CLM's nudges and the task message are user
    messages. CLM's rebuild after an edit turns every editable turn into plain text, so
    no signature survives it.
  - A text-only assistant turn carries none (CLM drops its tool_calls).

Per-turn accounting: each signed assistant turn carries the reasoning tokens of the call
that produced it (recorded by the hooks when the reply arrives, keyed by the signature).
A signature we never saw is estimated from its length (SIG_CHARS_PER_TOKEN, measured).

Open question (replay tests): a request resent minutes later did not carry the thinking.
quote_check reports the seconds between calls so this can be checked against the quotes.
"""

import hashlib
from typing import Callable

SIG_CHARS_PER_TOKEN = 5.1   # thought_signature base64 chars per thinking token (pilot 2)


def signature(m: dict) -> str | None:
    """The first thought signature on an assistant message's tool calls, if any."""
    for tc in m.get("tool_calls") or []:
        sig = (((tc or {}).get("extra_content") or {}).get("google") or {}).get("thought_signature")
        if sig:
            return sig
    return None


def sig_key(sig: str) -> str:
    return hashlib.sha1(sig.encode()).hexdigest()[:16]


def live_start(messages: list[dict]) -> int:
    """Index of the most recent user message that already has an assistant reply after it
    (-1 if none). Hidden thinking counts only for turns after it."""
    seen_assistant = False
    for i in range(len(messages) - 1, -1, -1):
        role = messages[i].get("role")
        if role == "assistant":
            seen_assistant = True
        elif role == "user" and seen_assistant:
            return i
    return -1


def hidden_per_message(messages: list[dict], thinking: dict[str, int] | None) -> list[int]:
    """Hidden thinking tokens Gemini reads at each message of this request."""
    if thinking is None:
        return [0] * len(messages)
    start = live_start(messages)
    out = []
    for i, m in enumerate(messages):
        sig = signature(m) if i > start and m.get("role") == "assistant" else None
        if sig is None:
            out.append(0)
        else:
            known = thinking.get(sig_key(sig))
            out.append(known if known is not None else round(len(sig) / SIG_CHARS_PER_TOKEN))
    return out


def provider_positions(messages: list[dict], count: Callable[[list[dict]], int], k: float,
                       thinking: dict[str, int] | None) -> list[float]:
    """pos[i] = tokens Gemini reads before message i (pos[len] = the whole request):
    CLM-tokenizer counts of the visible text scaled by k, plus the hidden thinking."""
    hidden = hidden_per_message(messages, thinking)
    out = [0.0]
    for m, h in zip(messages, hidden):
        out.append(out[-1] + count([m]) * k + h)
    return out
