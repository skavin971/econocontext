"""Render a chosen plan into an ordered request plus a content-addressed manifest.

Why it exists: every price assumed the request would look a certain way; the
assembler makes it so, deterministically, and records exactly what was sent.
What it must never do: choose. It renders what the optimizer chose, nothing else.
Earlier content changes only when the chosen plan says so (append, don't reorder).
Identical inputs give byte-identical output and an identical manifest.
"""

import hashlib
import json

from ..types import Manifest, RenderedRequest, Representation, Segment, Zone
from .zones import ORDER, assign, zoned_order


def render(window: list[Segment], plan: str, config_fingerprint: str,
           retrieved: list[Segment] | None = None, pointer_ids: set[str] | None = None,
           pointer_tokens: dict[str, int] | None = None, applied: bool = True) -> RenderedRequest:
    if plan == "ZONED":
        segments = zoned_order(window)
    else:
        segments = assign(list(window))  # AS_IS and the rest keep the host's order
    if plan == "COMMIT_PENDING" and pointer_ids:
        for s in segments:
            if s.id in pointer_ids:
                s.representation = Representation.POINTER
    if plan == "RETRIEVE_FROM_STORE" and retrieved:
        for s in retrieved:
            s.zone = Zone.VOLATILE  # appended at the tail: never breaks the cached prefix
        segments = segments + list(retrieved)
    bounds, start = {}, 0
    for zone in sorted(ORDER, key=ORDER.get):
        count = sum(1 for s in segments if s.zone == zone)
        bounds[zone.value] = (start, start + count)
        start += count
    # Provider-neutral breakpoint: after the last segment before the newest turn.
    stable = [s for s in segments if s.zone != Zone.VOLATILE]
    breakpoint_id = stable[-1].id if stable else None
    return RenderedRequest(segments=segments, zone_bounds=bounds,
                           cache_breakpoint_after_segment_id=breakpoint_id,
                           manifest=manifest(segments, config_fingerprint, pointer_tokens or {}),
                           applied=applied)


def manifest(segments: list[Segment], config_fingerprint: str,
             pointer_tokens: dict[str, int]) -> Manifest:
    zone_hashes = {}
    for zone in sorted(ORDER, key=ORDER.get):
        parts = [f"{s.id}:{s.representation.value}" for s in segments if s.zone == zone]
        zone_hashes[zone.value] = hashlib.sha256("\n".join(parts).encode()).hexdigest()
    tokens = sum(pointer_tokens.get(s.id, s.tokens) if s.representation == Representation.POINTER
                 else s.tokens for s in segments)
    versions = {s.source: s.version for s in segments if s.source and s.version}
    body = dict(zone_hashes=zone_hashes, segment_ids=[s.id for s in segments],
                versions=versions, token_estimate=tokens, config_fingerprint=config_fingerprint)
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    return Manifest(hash=digest, **body)


def pointer_text(segment: Segment, locator: str, preview_lines: int) -> str:
    """What a POINTER shows in the window: head and tail lines, and where the rest is.
    The full text stays in the Agent DB and at `locator`, which the agent can reopen
    with its own file tool."""
    lines = segment.text.splitlines()
    half = max(1, preview_lines // 2)
    if len(lines) <= preview_lines:
        preview = lines
    else:
        preview = lines[:half] + [f"... [{len(lines) - 2 * half} lines not shown] ..."] + lines[-half:]
    return ("\n".join(preview) + f"\n\n[Full output: {segment.tokens} tokens, {len(lines)} lines, "
            f"saved at {locator}. Read it with your file tool if you need more.]")
