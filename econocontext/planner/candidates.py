"""The operator catalog: every way EconoContext can carry out a step.

Why it exists: one place that says, per operator, where it applies, whether it is
exact (the model sees exactly what it would have seen) or approximate, whether it
is the host's own default, and whether it is generated yet.
What it must never do: generate or rank candidates (that is planner.py) or price
them (pricing/cost_model.py).
"""

from dataclasses import dataclass

from ..types import Intercept, OperatorType

EXACT, APPROX = OperatorType.EXACT, OperatorType.APPROXIMATE


@dataclass(frozen=True)
class Operator:
    name: str
    intercept: Intercept | None
    operator_type: OperatorType
    host_default: bool
    generated: bool          # False: named for completeness, not produced yet
    what: str


CATALOG = {op.name: op for op in (
    # before every model call
    Operator("AS_IS", Intercept.PLAN_PROMPT, EXACT, True, True,
             "send the request exactly as the host built it"),
    Operator("ZONED", Intercept.PLAN_PROMPT, EXACT, False, True,
             "same content in four-zone order (FROZEN, SLOW, WARM, VOLATILE) plus a cache breakpoint"),
    Operator("COMMIT_PENDING", Intercept.PLAN_PROMPT, APPROX, False, True,
             "apply queued pointer edits, only when the prefix is believed cold"),
    Operator("RETRIEVE_FROM_STORE", Intercept.PLAN_PROMPT, APPROX, False, True,
             "append keyword-matched stored segments not in the window, at the tail"),
    # before a tool runs
    Operator("RUN_TOOL", Intercept.BEFORE_TOOL_CALL, EXACT, True, True, "run the tool"),
    Operator("ANSWER_FROM_STORE", Intercept.BEFORE_TOOL_CALL, EXACT, False, True,
             "return the byte-identical earlier output of an identical call whose sources are unchanged"),
    # before a tool result enters a window
    Operator("KEEP_FULL", Intercept.ADMIT_TOOL_RESULT, EXACT, True, True, "admit the full result"),
    Operator("POINTER", Intercept.ADMIT_TOOL_RESULT, APPROX, False, True,
             "admit a preview plus a path the agent can reopen; full text stays in the store"),
    # before delegated work runs
    Operator("FRESH", Intercept.PLAN_DISPATCH, EXACT, True, True,
             "the host's own delegation: a new subagent instance"),
    Operator("REUSE_RESULT", Intercept.PLAN_DISPATCH, EXACT, False, True,
             "return the byte-identical stored result of the same task with an unchanged read-set"),
    # Named, not generated yet.
    # PLACEHOLDER: RESUME needs a host capability to continue a live subagent (Deep Agents'
    # isolated subagents keep no state; this needs a checkpointed subagent thread).
    Operator("RESUME", Intercept.PLAN_DISPATCH, EXACT, False, False,
             "continue an existing subagent that already holds the relevant context"),
    # PLACEHOLDER: FORK needs the host's fork mode (inherits the parent's history) and a
    # price for the inherited prefix.
    Operator("FORK", Intercept.PLAN_DISPATCH, EXACT, False, False,
             "start a subagent that inherits the parent's context"),
    # PLACEHOLDER: REPAIR needs per-worker read-sets and a way to refresh only what changed.
    Operator("REPAIR", Intercept.PLAN_DISPATCH, APPROX, False, False,
             "keep a worker's useful state and refresh only the sources that changed"),
)}


def operator(name: str) -> Operator:
    return CATALOG[name]
