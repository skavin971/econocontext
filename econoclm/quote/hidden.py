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

But Gemini does not always count it the same way. On Gate 4 (2026-10-03; exact hidden
part from countTokens on every logged request, runs/2026-10-03-main/validation) each call
was in one of three states, within 170 tokens:
  none   no hidden thinking counted                                     (48 of 208 calls)
  live   the rule above: signed turns after the last answered user msg  (100)
  all    every signed turn in the request, no reset                     (60)
So the state is MEASURED after each call (HiddenMeter):

  - raw = prompt_tokens - k * CLM's count of the request; the state is the one whose
    hidden total is nearest to raw (SNAP). On Gate 4 this is off by a median 40 tokens,
    p90 341 (vs 56 / 1,753 for the rule alone, 312 / 3,948 for assuming none).
  - k (Gemini tokens per CLM-tokenizer token of visible text) is recalibrated whenever the
    hidden part is known: calls with no signed turn at all (a run's first call, calls after
    the rebuild that follows an edit), and calls whose state is clear-cut (raw within the
    noise of one state and far from the others): k = (prompt - that state's hidden) / count.
  - Noise: NOISE_MIN tokens or NOISE_FRAC of the visible part, whichever is larger.
  - The quote and status line use the last measured state for the next call.

Each signed turn's thinking is the reasoning tokens of the call that produced it,
recorded when the reply arrives and keyed by its signature; a signature never seen is
estimated from its length (SIG_CHARS_PER_TOKEN, measured).
"""

import hashlib
from typing import Callable

SIG_CHARS_PER_TOKEN = 5.1   # thought_signature base64 chars per thinking token (pilot 2)
NOISE_MIN = 50              # tokens: calibration noise floor (see HiddenMeter)
NOISE_FRAC = 0.02           # ... or this share of the visible part, whichever is larger


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


MODES = ("none", "live", "all")


def hidden_per_message(messages: list[dict], thinking: dict[str, int] | None,
                       mode: str = "live") -> list[int]:
    """Hidden thinking tokens Gemini reads at each message of this request, in one of the
    three measured states (MODES)."""
    if thinking is None or mode == "none":
        return [0] * len(messages)
    start = live_start(messages) if mode == "live" else -1
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
                       thinking: dict[str, int] | None, mode: str = "live") -> list[float]:
    """pos[i] = tokens Gemini reads before message i (pos[len] = the whole request):
    CLM-tokenizer counts of the visible text scaled by k, plus the hidden thinking in the
    given state."""
    hidden = hidden_per_message(messages, thinking, mode)
    out = [0.0]
    for m, h in zip(messages, hidden):
        out.append(out[-1] + count([m]) * k + h)
    return out


class HiddenMeter:
    """Measures, call by call, which state Gemini is in and how much hidden thinking it read."""

    def __init__(self) -> None:
        self.k = 1.0
        self.calibrated = False
        self.mode = "live"            # the last measured state (used for the next quote)
        self.last = (0, 0, 0)         # (Gemini prompt tokens, CLM count, measured hidden)

    def observe(self, messages: list[dict], prompt: int | None, ours: int,
                thinking: dict[str, int]) -> int | None:
        """Update from one call (its request and billed prompt); returns the measured
        hidden tokens (None if the call reported no prompt tokens)."""
        if not prompt or not ours:
            return None
        totals = {m: sum(hidden_per_message(messages, thinking, m)) for m in MODES}
        if totals["all"] == 0:                     # no signed turn: nothing can be hidden
            self.k, self.calibrated = prompt / ours, True
            hidden = 0
        elif not self.calibrated:
            hidden = 0                             # cannot measure yet
        else:
            raw = prompt - self.k * ours
            noise = max(NOISE_MIN, NOISE_FRAC * self.k * ours)
            self.mode = min(MODES, key=lambda m: (abs(raw - totals[m]), MODES.index(m)))
            hidden = totals[self.mode]
            others = [abs(totals[m] - hidden) for m in MODES if totals[m] != hidden]
            if abs(raw - hidden) <= noise and all(o > 4 * noise for o in others):
                self.k = (prompt - hidden) / ours  # clear-cut: the hidden part is known
        self.last = (prompt, ours, hidden)
        return hidden
