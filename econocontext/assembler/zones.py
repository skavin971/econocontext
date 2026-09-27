"""Assign each segment to a prompt zone, and check message validity.

Why it exists: ordering a request by how fast its parts change (FROZEN, SLOW,
WARM, VOLATILE) keeps the stable part of the prompt at the front, where a
provider's prefix cache can serve it.
What it must never do: move a message ahead of something it depends on. Message
validity comes first: role order stays valid and every tool call is directly
followed by its results. Only free-standing messages (the task statement and
pinned plain messages) may move up into SLOW; tool calls and results never move.
"""

from ..types import Segment, SegmentKind, Zone

ORDER = {Zone.FROZEN: 0, Zone.SLOW: 1, Zone.WARM: 2, Zone.VOLATILE: 3}


def assign(segments: list[Segment]) -> list[Segment]:
    """Set each segment's zone in place and return the list."""
    last_assistant = max((i for i, s in enumerate(segments) if s.role == "assistant"), default=-1)
    leading = True
    for i, s in enumerate(segments):
        if leading and s.kind in (SegmentKind.SYSTEM, SegmentKind.TOOLS):
            s.zone = Zone.FROZEN
            continue
        leading = False
        movable = s.kind in (SegmentKind.TASK, SegmentKind.MESSAGE) and not s.pair_id
        if s.kind == SegmentKind.TASK or (s.pinned and movable and s.role != "assistant"):
            s.zone = Zone.SLOW
        elif i > last_assistant:
            s.zone = Zone.VOLATILE      # the newest turn: what the model is about to answer
        else:
            s.zone = Zone.WARM
    return segments


def zoned_order(segments: list[Segment]) -> list[Segment]:
    """Four-zone order. Stable within a zone, so chronology is kept."""
    return sorted(assign(list(segments)), key=lambda s: ORDER[s.zone])


def pairs_intact(segments: list[Segment]) -> bool:
    """Every tool call is directly followed by exactly its results, and no result is orphaned."""
    i = 0
    while i < len(segments):
        s = segments[i]
        if s.kind == SegmentKind.TOOL_CALL and s.pair_id:
            wanted = set(s.pair_id.split(","))
            j = i + 1
            while j < len(segments) and segments[j].kind == SegmentKind.TOOL_RESULT:
                wanted.discard(segments[j].pair_id)
                j += 1
            if wanted:
                return False
            i = j
            continue
        if s.kind == SegmentKind.TOOL_RESULT:
            return False  # a result not directly after its call
        i += 1
    return True
