"""The records the store's operations take and return: segments, and the forms a segment is shown in.

A segment's token count is given by the caller (the served model's exact count); this package does not
estimate tokens.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum


class Form(str, Enum):
    """How a segment is shown in the window."""
    FULL = "FULL"          # word for word
    SLICE = "SLICE"        # only some of its lines
    PREVIEW = "PREVIEW"    # its first and last lines
    NOTE = "NOTE"          # a short note in its place (e.g. "same output as above")
    EVICTED = "EVICTED"    # a one-line marker in its place
    SUMMARY = "SUMMARY"    # a summary standing in for earlier segments


class SegmentKind(str, Enum):
    SYSTEM = "system"
    TASK = "task"
    MESSAGE = "message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"


@dataclass
class Segment:
    id: str
    run_id: str
    native_id: str
    kind: SegmentKind
    text: str
    tokens: int
    content_hash: str
    source: str | None = None
    version: str | None = None
    pinned: bool = False
    needs_exact_bytes: bool = False
    pair_id: str | None = None
    role: str = "user"
    # its place in the window (set_window, window)
    position: int = 0
    part: int = 0
    in_window: bool = True
    form: Form = Form.FULL


def sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def make_segment(run_id: str, native_id: str, kind: SegmentKind, text: str, tokens: int, *, role: str,
                 source: str | None = None, version: str | None = None, pair_id: str | None = None,
                 pinned: bool = False, needs_exact_bytes: bool = False) -> Segment:
    """Identity: sha256(run_id | native_id | content_hash), so the same text under the same native id is
    the same segment."""
    content_hash = sha256(text)
    seg_id = sha256(f"{run_id}|{native_id}|{content_hash}")
    return Segment(id=seg_id, run_id=run_id, native_id=native_id, kind=kind, text=text, tokens=tokens,
                   content_hash=content_hash, source=source, version=version, pinned=pinned,
                   needs_exact_bytes=needs_exact_bytes, pair_id=pair_id, role=role)
