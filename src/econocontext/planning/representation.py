"""What answers a tool call: the result, or a bounded view of that same result.

Nothing here removes content from the run. The complete bytes stay in the store
behind the evidence and `original` references a bounded answer still carries,
and `truncated` says so on the payload's face. A call is never answered by a
different result -- only by less of its own.
"""

from ..assembler import token_count
from ..contracts import canonical

# The one unbounded field per tool. Everything else -- exit codes, evidence ids,
# paths -- is decisive at any size and is delivered whole.
BULK = {"read": "text", "command": "output", "test": "output"}
ELLIPSIS = "\n...[truncated; complete bytes retained in the store]...\n"


def finding_stub(operation, margin):
    """The largest finding the operation's contract permits.

    Projection uses this so a projection can never be smaller than the delivery
    it stands for; `validate_result` enforces the same ceiling before a real
    finding is published.
    """
    room = max(0, int(operation.result_tokens * 3 / margin) - 48)
    return dict(answer="x" * room, evidence=["0" * 32])


def shrink(text, budget):
    """Keep both ends of a long output.

    A failure's cause is as often at the tail as the head: pytest puts its
    summary last, so keeping only a prefix discards the decisive line.
    """
    if budget <= 0:
        return ""
    if len(text) <= budget:
        return text
    if budget <= len(ELLIPSIS):
        return text[:budget]
    room = budget - len(ELLIPSIS)
    head = room // 2
    return text[:head] + ELLIPSIS + text[-(room - head) :]


def represent(name, output, finding, chars):
    """The tool's own schema with only its unbounded field shrunk."""
    payload = dict(output)
    field = BULK.get(name)
    if field and isinstance(output.get(field), str):
        payload[field] = shrink(output[field], chars)
        payload["truncated"] = payload.get("truncated", False) or len(payload[field]) < len(
            output[field]
        )
    elif name == "search" and output.get("matches"):
        share = max(200, chars // max(len(output["matches"]), 1))
        payload["matches"] = [
            dict(match, excerpt=shrink(match.get("excerpt", ""), share))
            for match in output["matches"]
        ]
        payload["truncated"] = True
    payload["finding"] = finding["answer"]
    payload["finding_evidence"] = list(finding["evidence"])
    return payload


def message(call_id, payload):
    return dict(role="tool", tool_call_id=call_id, content=canonical(payload))


def excerpt_chars(call_id, name, output, operation, margin):
    """Characters of bulk a delegated answer may carry.

    The excerpt is funded from the same halved allowance as the finding, so a
    delegated answer cannot reach the answer it replaces however the two split.
    Escaping expansion is text-dependent, so the fit is measured rather than
    modelled -- the same discipline as the assembler's exact-token reselection.
    """
    allowance = token_count(message(call_id, output), margin) // 2
    stub = finding_stub(operation, margin)

    def built(chars):
        return token_count(message(call_id, represent(name, output, stub, chars)), margin)

    fixed = built(0)
    chars = max(0, int((allowance - fixed) * 3 / margin))
    while chars > 0 and built(chars) > allowance:
        chars = int(chars * 0.9) - 1
    return max(chars, 0)
