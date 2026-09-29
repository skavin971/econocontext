"""Guesses about the future that the cost model needs: how many more turns an agent
will run, and how likely a piece of context is to be needed again.

Why it exists: "what it leaves behind" costs tokens on every remaining turn, and
a pointer is only cheap if its content is unlikely to be needed back.
What it must never do: look at the provider or the price.

PLACEHOLDER: every formula here.
- remaining_turns = max(1, config default - turns already taken)
- future_use_score(segment) = the configured prior for its kind (1.0 when pinned
  or when exact bytes are needed); length is used only to break ties by making
  very large tool results slightly less likely to be needed in full
- p_need_again = future_use_score, clamped to [0, 1]
The future version learns these from the Agent DB: which stored segments were
retrieved or reopened later, per kind, per task stage.
"""

from ..types import AgentNode, Segment


# PLACEHOLDER: every formula in this module (documented above); later learned from the Agent DB.
def remaining_turns(agent: AgentNode, default: int) -> int:
    return max(1, default - agent.turns)


def future_use_score(segment: Segment, kind_need_again: dict[str, float]) -> float:
    if segment.pinned or segment.needs_exact_bytes:
        return 1.0
    return kind_need_again[segment.kind.value]


def p_need_again(score: float) -> float:
    return max(0.0, min(1.0, score))
