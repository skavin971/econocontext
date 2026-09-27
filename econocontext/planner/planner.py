"""Rule-based candidate generation, one function per intercept.

Why it exists: the optimizer can only choose among plans somebody proposed. Every
intercept always includes the host's default behaviour as a candidate, so the
optimizer can always decline to change anything.
What it must never do: price or choose (the cost model and optimizer do), or
propose an operator the catalog marks as not generated.

PLACEHOLDER: fixed rules. The future planner generates candidates from learned
predictions (which stored content is worth retrieving, which results are worth
pointering) and expands RESUME / FORK / REPAIR once the host supports them.
"""

from ..pricing.predictor import future_use_score, p_need_again
from ..tokens import count_tokens
from ..types import Candidate, DispatchIntent, PlanContext, Segment, ToolCallEvent
from .candidates import operator


def _candidate(name: str, cfg: dict, **payload) -> Candidate:
    op = operator(name)
    risk = cfg["quality_risk"].get(name, 0.0)
    return Candidate(name=name, operator_type=op.operator_type, quality_risk=risk,
                     is_host_default=op.host_default, payload=payload)


# PLACEHOLDER: fixed rules per intercept; later generated from learned predictions.
def for_prompt(ctx: PlanContext, cfg: dict, retrieved: list[Segment],
               pending_pointer_tokens: int, prefix_cold: bool) -> list[Candidate]:
    """Before every model call: how to send this agent's window."""
    tokens = sum(s.tokens for s in ctx.window)
    common = dict(send_tokens=tokens, model_calls=1, resident_tokens=tokens)
    candidates = [_candidate("AS_IS", cfg, **common),
                  _candidate("ZONED", cfg, **common)]
    # PLACEHOLDER: nothing queues pointer edits yet (pointers are made at arrival),
    # so COMMIT_PENDING is dormant until retroactive edits exist.
    if pending_pointer_tokens and prefix_cold:
        saved = pending_pointer_tokens
        candidates.append(_candidate("COMMIT_PENDING", cfg, send_tokens=tokens - saved,
                                     model_calls=1, resident_tokens=tokens - saved))
    if retrieved:
        extra = sum(s.tokens for s in retrieved)
        # Appended to the VOLATILE tail for this call only: sent now, not left resident.
        candidates.append(_candidate("RETRIEVE_FROM_STORE", cfg, send_tokens=tokens + extra,
                                     model_calls=1, resident_tokens=tokens,
                                     segment_ids=[s.id for s in retrieved]))
    return candidates


def for_tool_call(ctx: PlanContext, cfg: dict, event: ToolCallEvent,
                  stored: dict | None) -> list[Candidate]:
    """Before a tool runs: run it, or answer from the store."""
    size = count_tokens(stored["text"]) if stored else 0
    candidates = [_candidate("RUN_TOOL", cfg, runs_tool=True, result_tokens=size,
                             resident_tokens=size, side_effect=event.side_effect)]
    if stored is not None:
        candidates.append(_candidate(
            "ANSWER_FROM_STORE", cfg, from_store=True, result_tokens=size, resident_tokens=size,
            tool_result_id=stored["tool_result_id"], read_set=stored["read_set"],
            side_effect=event.side_effect))
    return candidates


def prior_p_need_again(segment: Segment, cfg: dict) -> float:
    """The default (no --jev) prediction: the fixed guess per kind, from config."""
    return p_need_again(future_use_score(segment, cfg["predictor"]["kind_need_again"],
                                         cfg["planner"]["pointer_min_tokens"]))


def for_tool_result(ctx: PlanContext, cfg: dict, segment: Segment,
                    pointer_tokens: int, p_need: float) -> list[Candidate]:
    """Before a tool result enters a window: the full result, or a pointer.

    `p_need` (probability the content is needed again) comes from the engine: the
    fixed guess above by default, or Jev with --jev (planner/jev_planner.py).
    """
    full = segment.tokens
    candidates = [_candidate("KEEP_FULL", cfg, result_tokens=full, resident_tokens=full)]
    if full >= cfg["planner"]["pointer_min_tokens"]:
        candidates.append(_candidate(
            "POINTER", cfg, result_tokens=pointer_tokens, resident_tokens=pointer_tokens,
            reread_tokens=full, p_need_again=p_need,
            needs_exact_bytes=segment.needs_exact_bytes))
    return candidates


def for_dispatch(ctx: PlanContext, cfg: dict, intent: DispatchIntent,
                 stored: dict | None) -> list[Candidate]:
    """Before delegated work runs: a fresh subagent, or a stored result."""
    model = cfg["cost_model"]
    size = count_tokens(stored["result_text"]) if stored else 0
    candidates = [_candidate("FRESH", cfg, extra_calls=model["fresh_expected_calls"],
                             extra_call_input_tokens=model["fresh_expected_input_tokens_per_call"],
                             result_tokens=size, resident_tokens=size)]
    if stored is not None:
        candidates.append(_candidate(
            "REUSE_RESULT", cfg, from_store=True, result_tokens=size, resident_tokens=size,
            result_id=stored["result_id"], read_set=stored["read_set"], side_effect=False))
    return candidates
