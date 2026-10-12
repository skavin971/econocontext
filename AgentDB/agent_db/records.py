"""The records the contract's operations take and return, as this package's own definitions.

They mirror econocontext.types and econocontext.evidence (field names, enum values, identity
rules) so that either package's objects can be passed in: the stores read attributes, never
classes. Having them here lets the stores import without EconoContext installed, and without
colliding with the Tier A package that reuses the name `econocontext` for different code.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


def count_tokens(text: str) -> int:
    """EconoContext's estimate when it is importable, else the same order of magnitude."""
    try:
        from econocontext.tokens import count_tokens as upstream
        return upstream(text)
    except Exception:  # noqa: BLE001
        return max(1, len(text.encode()) // 4)


class Zone(str, Enum):
    FROZEN = "FROZEN"
    SLOW = "SLOW"
    WARM = "WARM"
    VOLATILE = "VOLATILE"


class Representation(str, Enum):
    FULL = "FULL"
    POINTER = "POINTER"
    COMPRESSED = "COMPRESSED"
    STRUCTURED = "STRUCTURED"


class SegmentKind(str, Enum):
    SYSTEM = "system"
    TOOLS = "tools"
    TASK = "task"
    MESSAGE = "message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SUBAGENT_RESULT = "subagent_result"


class AgentStatus(str, Enum):
    BUSY = "busy"
    IDLE = "idle"
    RETIRED = "retired"


class OperatorType(str, Enum):
    EXACT = "exact"
    APPROXIMATE = "approximate"


class Intercept(str, Enum):
    PLAN_PROMPT = "plan_prompt"
    BEFORE_TOOL_CALL = "before_tool_call"
    ADMIT_TOOL_RESULT = "admit_tool_result"
    PLAN_DISPATCH = "plan_dispatch"


@dataclass
class Segment:
    id: str
    run_id: str
    agent_id: str
    native_id: str
    kind: SegmentKind
    text: str
    tokens: int
    content_hash: str
    zone: Zone = Zone.WARM
    representation: Representation = Representation.FULL
    source: str | None = None
    version: str | None = None
    pinned: bool = False
    needs_exact_bytes: bool = False
    pair_id: str | None = None
    role: str = "user"
    position: int = 0
    in_window: bool = True


@dataclass
class AgentNode:
    agent_id: str
    parent_id: str | None
    subagent_type: str | None
    status: AgentStatus = AgentStatus.BUSY
    window_max_tokens: int | None = None
    read_set: dict[str, str] = field(default_factory=dict)
    side_effect: bool = False
    turns: int = 0


@dataclass
class ProviderUsage:
    uncached_input: int | None
    cache_read: int | None
    cache_write: int | None
    output: int | None
    reasoning: int | None = None
    cache_write_1h: int | None = None
    latency_ms: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    cache_write_applicable: bool = True


@dataclass
class CostBreakdown:
    prepare: float = 0.0
    work: float = 0.0
    integrate: float = 0.0
    leaves_behind: float = 0.0
    latency_ms: float = 0.0


@dataclass
class Candidate:
    name: str
    operator_type: OperatorType
    quality_risk: float = 0.0
    is_host_default: bool = False
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class Rejection:
    name: str
    why_not: str


@dataclass
class Decision:
    id: str
    intercept: Intercept
    chosen: Candidate
    cost: CostBreakdown
    rejected: list[Rejection]
    feasible: list[str]
    candidate_costs: dict[str, CostBreakdown]
    applied: bool = False
    created_at: str = ""
    prediction: dict | None = None
    subject_id: str | None = None
    payloads: dict[str, dict] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    source_kind: str
    source_key: str
    source_version: str
    range: str
    content_hash: str
    byte_size: int
    token_size: int
    recoverable: bool


@dataclass(frozen=True)
class EvidenceEvent:
    event: str
    tool_name: str
    args_key: str
    source_key: str
    ref: EvidenceRef | None = None


def sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def make_segment(run_id: str, agent_id: str, native_id: str, kind: SegmentKind, text: str, *,
                 role: str, source: str | None = None, version: str | None = None,
                 pair_id: str | None = None, pinned: bool = False,
                 needs_exact_bytes: bool = False) -> Segment:
    """Identity as EconoContext defines it: sha256(agent_id | native_id | content_hash)."""
    content_hash = sha256(text)
    seg_id = sha256(f"{agent_id}|{native_id}|{content_hash}")
    return Segment(id=seg_id, run_id=run_id, agent_id=agent_id, native_id=native_id, kind=kind,
                   text=text, tokens=count_tokens(text), content_hash=content_hash, source=source,
                   version=version, pinned=pinned, needs_exact_bytes=needs_exact_bytes,
                   pair_id=pair_id, role=role)


def make_ref(source_kind: str, source_key: str, source_version: str, content: str,
             range: str = "", recoverable: bool = True) -> EvidenceRef:
    """Evidence identity as EconoContext defines it: sha256(kind | key | version | range)."""
    evidence_id = sha256(f"{source_kind}|{source_key}|{source_version}|{range}")
    return EvidenceRef(evidence_id, source_kind, source_key, source_version, range,
                       sha256(content), len(content.encode()), count_tokens(content), recoverable)
