"""Jev planner: predicts whether a tool result will be needed again. Not built yet.

Called by `EconoContext._predict` (engine.py) at `admit_tool_result`, only in runs
started with `--jev`. It replaces the fixed guess `planner.prior_p_need_again`
(config `predictor.kind_need_again`: 0.3 for a tool result, scaled down above
`planner.pointer_min_tokens`). It gets the same inputs the original planner has.

Contract
    Return p in [0, 1]: the probability the agent needs this result's content again
    later in the task. The engine uses it in the POINTER candidate's cost
    (prepare += p x segment.tokens; see pricing/cost_model.py). Raise on any
    failure: the engine falls back to `prior`, logs the reason, and the agent
    keeps running. Every decision logs {p_need_again, source, prior} in
    `decisions.prediction`, so --jev and non-Jev runs can be compared.

Inputs
    segment  the new tool result (text, tokens, kind, source path, pair_id)
    event    the tool call behind it (tool_name, args_key, source, reads, side_effect)
    ctx      the planning context (types.PlanContext):
               window           this agent's full window as sent on its last model
                                call: system, tools, task, every message, tool call
                                and tool result so far, in order. It does not yet
                                contain `segment` or the assistant turn that
                                requested it (both arrive with the next model call).
               agent            AgentNode: turns taken, parent, read_set, side_effect
               remaining_turns  the cost model's estimate of turns left
               current_versions source -> version (what has been edited since)
    cfg      the loaded config/econocontext.yaml (add a `jev:` section for your knobs)
    prior    the fixed guess for this segment, if you want it as a feature

Jev API (docs.typesafe.ai/api.md, read 2026-09-27)
    POST https://api.typesafe.ai/v1/systemone, Authorization: Bearer $TYPESAFE_API_KEY
    {"model": "jev-latest", "state": <str | object>,
     "questions": {"needed_again": {"type": "noul", "instructions": "..."}}}
    -> {"answers": {"needed_again": {"type": "noul", "noul": 0.82}}, "usage": {...}}
    Errors 401/422/429/529; back off on 429/529. Context limit 32K tokens per
    AI/ML API's page (TODO: verify). Price $0.042/MTok input, output free.

Constraints
    - Core rule: standard library only (urllib), no provider SDK; test_isolation.py
      enforces the SDK part.
    - Key from the environment only; never log it or put it in exceptions.
    - Keep it fast (it runs on every tool result): short timeout, truncate `state`.
    - Jev's own cost is not counted in run cost (out of scope for now).

Test
    tests/test_planner_jev.py: unit tests (Jev faked), one live call (-m live), and
    the commands to run both tracks on SWE-bench and compare them in report.py.
"""

from ..types import PlanContext, Segment, ToolResultEvent


def p_need_again(segment: Segment, event: ToolResultEvent, ctx: PlanContext, cfg: dict,
                 prior: float) -> float:
    # PLACEHOLDER: not built. See the contract above.
    raise NotImplementedError("jev_planner.p_need_again is not built yet")
