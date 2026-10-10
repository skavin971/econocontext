"""Rule-based candidate generation, one function per intercept.

Why it exists: the optimizer can only choose among plans somebody proposed. Every
intercept always includes the host's default behaviour as a candidate, so the
optimizer can always decline to change anything.
What it must never do: price or choose (the cost model and optimizer do), or
propose an operator the catalog marks as not generated.

Rules, fed by predictions: H and p are learned from earlier runs when a History is
given (learn/predictors.py), else the config guesses. POINTER is offered whenever a
pointer is shorter than the result; COMMIT_PENDING when old results are worth pointing
out now (stale_edits); RESUME when an idle worker already holds files a task needs.
"""

from copy import copy
from dataclasses import asdict

from ..assembler.zones import assign
from ..costmodel import options
from ..costmodel.options import Shape
from ..pricing.predictor import future_use_score, p_need_again
from ..tokens import count_tokens
from ..types import (Candidate, DispatchIntent, PlanContext, Representation, Segment, SegmentKind,
                     ToolCallEvent, Zone)
from .candidates import operator


def _candidate(name: str, cfg: dict, **payload) -> Candidate:
    op = operator(name)
    risk = cfg["quality_risk"].get(name, 0.0)
    return Candidate(name=name, operator_type=op.operator_type, quality_risk=risk,
                     is_host_default=op.host_default, payload=payload)


# PLACEHOLDER: fixed rules per intercept; later generated from learned predictions.
def for_prompt(ctx: PlanContext, cfg: dict, retrieved: list[Segment],
               edits: list[dict], cache_warm: bool) -> list[Candidate]:
    """Before every model call: how to send this agent's window.

    `edits` (from stale_edits) are old tool results worth replacing with pointers now."""
    tokens = sum(s.tokens for s in ctx.window)
    common = dict(send_tokens=tokens, model_calls=1, resident_tokens=tokens)
    candidates = [_candidate("AS_IS", cfg, **common),
                  _candidate("ZONED", cfg, **common)]
    if edits:
        saved = sum(e["full"] - e["pointer"] for e in edits)
        first = min(e["position"] for e in edits)
        suffix = sum(s.tokens for s in ctx.window[first:])
        candidates.append(_candidate(
            "COMMIT_PENDING", cfg, send_tokens=tokens - saved, model_calls=1,
            resident_tokens=tokens - saved,
            reread_tokens=sum(e["p"] * e["full"] for e in edits), p_need_again=1.0,
            extra_calls=sum(e["p"] for e in edits), extra_call_input_tokens=tokens,
            cache_break_tokens=suffix if cache_warm else 0,
            segment_ids=[e["segment_id"] for e in edits]))
    if retrieved:
        extra = sum(s.tokens for s in retrieved)
        # Appended to the VOLATILE tail for this call only: sent now, not left resident.
        candidates.append(_candidate("RETRIEVE_FROM_STORE", cfg, send_tokens=tokens + extra,
                                     model_calls=1, resident_tokens=tokens,
                                     segment_ids=[s.id for s in retrieved]))
    return candidates


def stale_edits(ctx: PlanContext, window: list[Segment], arrival: dict[str, int],
                calls_so_far: int, p_of, pointer_len) -> list[dict]:
    """Old tool results whose pointer saves more than it is expected to cost.

    For each full tool result outside the newest turn, idle for `age` calls:
      gain = (full - pointer) x H x rate  -  p x (full + window)  -  p x full x H x rate
    (residency saved, minus the expected re-read, its extra call, and its residency again),
    with p = p_of(tool, age). Kept when gain > 0; the optimizer then weighs the whole set
    against the one-time cache break."""
    tokens = sum(s.tokens for s in window)
    h, rate = ctx.remaining_turns, ctx.resident_rate
    zoned = assign([copy(s) for s in window])
    edits = []
    for position, s in enumerate(zoned):
        if (s.kind != SegmentKind.TOOL_RESULT or s.zone == Zone.VOLATILE
                or s.representation == Representation.POINTER):
            continue
        pointer = pointer_len(s)
        if pointer >= s.tokens:
            continue
        age = max(0, calls_so_far - arrival.get(s.id, calls_so_far))
        p = p_of((s.source or "").removeprefix("tool:"), age)
        gain = (s.tokens - pointer) * h * rate - p * (s.tokens + tokens) - p * s.tokens * h * rate
        if gain > 0:
            edits.append(dict(segment_id=s.id, position=position, full=s.tokens,
                              pointer=pointer, p=p, age=age))
    return edits


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
    return p_need_again(future_use_score(segment, cfg["predictor"]["kind_need_again"]))


def for_tool_result(ctx: PlanContext, cfg: dict, segment: Segment,
                    pointer_tokens: int, p_need: float,
                    window_tokens: int | None = None) -> list[Candidate]:
    """Before a tool result enters a window: the full result, or a pointer.

    `p_need` (probability the content is needed again) comes from the engine: the
    fixed guess above by default, or Jev with --jev (planner/jev_planner.py).
    """
    full = segment.tokens
    candidates = [_candidate("KEEP_FULL", cfg, result_tokens=full, resident_tokens=full)]
    if pointer_tokens < full:  # offered whenever a pointer is shorter; the optimizer decides
        # Reopening it costs one expected extra model call that re-sends the window.
        window = sum(s.tokens for s in ctx.window) if window_tokens is None else window_tokens
        candidates.append(_candidate(
            "POINTER", cfg, result_tokens=pointer_tokens, resident_tokens=pointer_tokens,
            reread_tokens=full, p_need_again=p_need, extra_calls=p_need,
            extra_call_input_tokens=window, needs_exact_bytes=segment.needs_exact_bytes))
    return candidates


def for_placement(ctx: PlanContext, cfg: dict, task: str, workers: list[dict],
                  calls_hat: float, file_tokens: dict[str, int], shape: Shape, cache,
                  need_named_files: bool = True,
                  briefs: dict[str, float] | None = None) -> list[Candidate]:
    """Before a sub-task is delegated: a new worker (FRESH), the idle worker that
    already holds the most of the files the task names (RESUME), or a new worker given
    the lines an idle worker read (HANDOFF; `briefs`: worker id -> brief tokens). With
    need_named_files=False (Claude Code, whose sub-agent prompts rarely name files) any
    idle worker is a candidate; the price still decides.

    Each option is priced by the meter as a loop of `calls_hat` calls of the harness's
    worker shape (costmodel/options.py): a new worker starts from the shared cached
    prefix, a resumed one from its whole history, which it then sends on every call.
    A worker idle past the cache lifetime is never a candidate: its whole history would
    be written again (1.25x) on the first call, which alone cost more than a new worker's
    whole run in every case observed (wres4, docs/omnigent-findings.md).
    Until a forecast predicts that a resumed worker needs fewer calls, both options get
    the same count, and RESUME wins only if its history is smaller than the new
    worker's first prompt."""
    task_tokens = count_tokens(task)
    named = {p for p in file_tokens if p in task or p.rsplit("/", 1)[-1] in task}

    def priced(name: str, loop, **payload) -> Candidate:
        return _candidate(name, cfg, meter_nu=cache.price(loop, ctx.rates), loop=asdict(loop), **payload)

    candidates = [priced("FRESH", options.fresh(shape, task_tokens, calls_hat))]
    free = [w for w in workers if not w["busy"] and w["warm"] and w["title"]
            and (set(w["files"]) & named or not need_named_files)]
    if free:
        best = max(free, key=lambda w: sum(file_tokens[p] for p in set(w["files"]) & named))
        candidates.append(priced(
            "RESUME", options.resume(shape, best["resident_tokens"], True, task_tokens, calls_hat),
            title=best["title"], worker_id=best["worker_id"],
            held_tokens=sum(file_tokens[p] for p in set(best["files"]) & named)))
    if briefs:
        source = max(briefs, key=briefs.get)
        candidates.append(priced("HANDOFF", options.handoff(shape, task_tokens, briefs[source], calls_hat),
                                 worker_id=source, brief_tokens=briefs[source]))
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
