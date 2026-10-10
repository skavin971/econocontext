"""Ask Jev whether a tool result will be needed again.

Called by `EconoContext._predict` (engine.py) at `admit_tool_result`, only in runs
started with `--jev`. It replaces the fixed guess `planner.prior_p_need_again`
(config `predictor.kind_need_again`: 0.3 for a tool result, scaled down above
a pointer shorter than the result). It gets the same inputs the original planner has.

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

Jev API (https://docs.typesafe.ai/api.md, checked 2026-09-29)
    POST https://api.typesafe.ai/v1/systemone, Authorization: Bearer $TYPESAFE_API_KEY
    {"model": "jev-latest", "state": <str | object>,
     "questions": {"needed_again": {"type": "noul", "instructions": "..."}}}
    -> {"answers": {"needed_again": {"type": "noul", "noul": 0.82}}, "usage": {...}}
    This first version sends all five arguments as structured state, including the
    full window, without truncation. It makes one request with the configured
    timeout; API errors (including oversized input) use the engine's fallback.
    The key comes only from TYPESAFE_API_KEY, never from cfg or the request state.
    Uses stdlib urllib to preserve core isolation. Jev cost is not yet accounted for.

Test
    tests/test_planner_jev.py: unit tests (Jev faked), one live call (-m live), and
    the commands to run both tracks on SWE-bench and compare them in report.py.
"""

import json
import os
from dataclasses import asdict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..types import PlanContext, Segment, ToolResultEvent


def p_need_again(segment: Segment, event: ToolResultEvent, ctx: PlanContext, cfg: dict,
                 prior: float, usage: dict | None = None) -> float:
    """Send the provided context to Jev and return its probability of future use.

    If `usage` is given, Jev's reported token usage is copied into it (for its cost)."""
    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        raise RuntimeError("TYPESAFE_API_KEY is not set")

    settings = cfg["jev"]
    payload = {
        "model": settings["model"],
        "state": {
            "segment": asdict(segment),
            "event": asdict(event),
            "ctx": asdict(ctx),
            "cfg": cfg,
            "prior": prior,
        },
        "questions": {
            "needed_again": {
                "type": "noul",
                "instructions": (
                    "Given the agent's task and conversation in `ctx.window`, will the "
                    "agent need the information in the new tool result `segment.text` "
                    "again later to complete its current task? `event` describes the "
                    "tool result. The window is from the last model call, before the "
                    "assistant requested this result. `prior` is a heuristic estimate."
                ),
            },
        },
    }
    request = Request(
        "https://api.typesafe.ai/v1/systemone",
        data=json.dumps(payload, allow_nan=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings["timeout_seconds"]) as response:
            reply = json.load(response)
        answer = reply["answers"]["needed_again"]
        answer_type, probability = answer["type"], answer["noul"]
    except HTTPError as exc:
        # The engine logs exceptions: don't include response bodies or credentials.
        raise RuntimeError(f"Jev HTTP {exc.code}") from None
    except (URLError, TimeoutError):
        raise RuntimeError("Jev request failed or timed out") from None
    except (ValueError, KeyError, TypeError):
        raise ValueError("Jev returned an invalid response") from None

    if (answer_type != "noul" or type(probability) not in (int, float)
            or not 0.0 <= probability <= 1.0):
        raise ValueError("Jev returned an invalid probability")
    if usage is not None and isinstance(reply.get("usage"), dict):
        usage.update(reply["usage"])
    return float(probability)
