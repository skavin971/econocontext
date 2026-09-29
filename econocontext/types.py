"""Every dataclass and enum shared between EconoContext components.

Why it exists: components are isolated and talk only through these records, so any
one of them can be replaced without touching the others.
What it must never do: import anything outside the standard library, or hold logic
beyond trivial derived properties.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Zone(str, Enum):
    FROZEN = "FROZEN"      # instructions and tool schemas; identical for a worker's life
    SLOW = "SLOW"          # task statement and pinned constraints
    WARM = "WARM"          # conversation history, append-only and chronological
    VOLATILE = "VOLATILE"  # the newest turn, plus anything retrieved this turn


class Representation(str, Enum):
    FULL = "FULL"
    POINTER = "POINTER"        # a short preview plus a path the agent can reopen
    COMPRESSED = "COMPRESSED"  # reserved: not implemented this phase
    STRUCTURED = "STRUCTURED"  # reserved: not implemented this phase


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
    EXACT = "exact"              # the model sees exactly what it would have seen
    APPROXIMATE = "approximate"  # the model sees something different (e.g. a pointer)


class Intercept(str, Enum):
    PLAN_PROMPT = "plan_prompt"
    BEFORE_TOOL_CALL = "before_tool_call"
    ADMIT_TOOL_RESULT = "admit_tool_result"
    PLAN_DISPATCH = "plan_dispatch"


class Mode(str, Enum):
    OBSERVE = "observe"      # decide and log, but always return the host default
    AUTOPILOT = "autopilot"  # apply decisions


class Objective(str, Enum):
    COST = "cost"
    LATENCY = "latency"
    BALANCED = "balanced"


@dataclass
class Segment:
    """One piece of context in one agent's window. Identity is (agent_id, native_id,
    content_hash): written once, never rewritten. Window state (position, in_window,
    zone, representation) can change and is stored separately."""
    id: str                          # sha256(agent_id | native_id | content_hash)
    run_id: str
    agent_id: str
    native_id: str                   # host id: message id or tool-call id (or a positional id)
    kind: SegmentKind
    text: str                        # full text, always
    tokens: int                      # estimate from econocontext.tokens.count_tokens
    content_hash: str                # sha256(text)
    zone: Zone = Zone.WARM
    representation: Representation = Representation.FULL
    source: str | None = None        # file path or 'tool:<name>' this content came from
    version: str | None = None       # version of `source` when captured
    pinned: bool = False             # must stay in the window
    needs_exact_bytes: bool = False  # a pointer or paraphrase will not do
    pair_id: str | None = None       # tool-call id(s) linking a call to its result; a tool_call
                                     # segment carrying several calls lists them comma-separated
    # Added beyond the spec's field list: message validity (role order, calls followed by
    # their results) cannot be checked without knowing who sent each segment.
    role: str = "user"               # system | user | assistant | tool
    position: int = 0                # order within the window
    in_window: bool = True


@dataclass
class AgentNode:
    agent_id: str
    parent_id: str | None
    subagent_type: str | None        # None for the root agent
    status: AgentStatus = AgentStatus.BUSY
    window_max_tokens: int | None = None
    read_set: dict[str, str] = field(default_factory=dict)  # source -> version it read
    side_effect: bool = False        # it wrote a file or ran a command
    turns: int = 0


@dataclass
class CacheState:
    last_prefix_hashes: list[str] = field(default_factory=list)
    last_prefix_tokens: list[int] = field(default_factory=list)
    last_sent_at: float | None = None       # epoch seconds
    ttl_seconds: float | None = None
    observed_hit_ratio: float | None = None  # reported / predicted, when predictions existed
    predicted_total: int = 0
    reported_total: int = 0


@dataclass
class ProviderUsage:
    """Provider-neutral usage for one model call. None = not reported, never zero."""
    uncached_input: int | None
    cache_read: int | None
    cache_write: int | None
    output: int | None               # includes reasoning
    reasoning: int | None = None     # subset of output, for explanation only
    cache_write_1h: int | None = None  # Anthropic 1-hour writes, when reported
    latency_ms: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)  # original fields, for audit
    # False means the provider/cache mode has no separately billed write counter.
    # It is distinct from cache_write=None, which otherwise means "not reported".
    cache_write_applicable: bool = True

    @property
    def prompt_tokens(self) -> int | None:
        parts = (self.uncached_input, self.cache_read, self.cache_write, self.cache_write_1h)
        return None if self.uncached_input is None else sum(p or 0 for p in parts)


@dataclass
class HostRequest:
    agent_id: str
    segments: list[Segment]
    window_max_tokens: int | None = None


@dataclass
class Manifest:
    zone_hashes: dict[str, str]
    segment_ids: list[str]
    versions: dict[str, str]
    token_estimate: int
    config_fingerprint: str
    hash: str


@dataclass
class RenderedRequest:
    segments: list[Segment]
    zone_bounds: dict[str, tuple[int, int]]  # zone -> [start, end) indexes into segments
    cache_breakpoint_after_segment_id: str | None
    manifest: Manifest
    applied: bool                    # False: the host's own request is used unchanged
    decision_id: str | None = None


@dataclass
class ToolCallEvent:
    agent_id: str
    call_id: str
    tool_name: str
    args: dict[str, Any]
    args_key: str                    # sha256 of the normalized arguments
    side_effect: bool                # writes or executes: never answered from the store


@dataclass
class ToolResultEvent:
    agent_id: str
    call_id: str
    tool_name: str
    args_key: str
    text: str
    source: str | None
    # The sources this result depends on: [path] for a file read, ["*"] (the workspace)
    # for a search or listing. The engine attaches their current versions.
    reads: list[str]
    side_effect: bool
    needs_exact_bytes: bool = False


@dataclass
class DispatchIntent:
    agent_id: str                    # the agent asking for delegated work
    call_id: str
    subagent_type: str
    description: str
    task_key: str                    # sha256(subagent_type + normalized description)


@dataclass
class ToolAction:
    run_tool: bool                   # True: let the host run the tool
    stored_text: str | None = None   # byte-identical earlier output, when answered from the store
    tool_result_id: str | None = None
    decision_id: str | None = None


@dataclass
class AdmitResult:
    segment: Segment                 # the stored, full segment
    rendered_text: str               # what enters the window (full text, or pointer text)
    decision_id: str | None = None


@dataclass
class DispatchResult:
    """What the host's default delegation produced."""
    result_text: str
    subagent_id: str


@dataclass
class DispatchOutcome:
    result_text: str
    reused: bool
    result_id: str | None = None
    decision_id: str | None = None


@dataclass
class CostBreakdown:
    """Predicted cost in normalized units (NU). Latency is kept apart, never folded in."""
    prepare: float = 0.0             # getting ready: tokens sent, retrieval, re-reads
    work: float = 0.0                # doing the work: model output, delegated calls
    integrate: float = 0.0           # taking it back: tokens the result adds to the receiver
    leaves_behind: float = 0.0       # what stays resident, times the turns it will be re-sent
    latency_ms: float = 0.0

    @property
    def total(self) -> float:
        return self.prepare + self.work + self.integrate + self.leaves_behind


@dataclass
class Candidate:
    name: str
    operator_type: OperatorType
    quality_risk: float = 0.0
    is_host_default: bool = False
    payload: dict[str, Any] = field(default_factory=dict)  # sizes and ids the cost model reads


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
    feasible: list[str]              # every candidate that passed all gates
    candidate_costs: dict[str, CostBreakdown]
    applied: bool = False
    created_at: str = ""
    # admit_tool_result only: {"p_need_again", "source" (prior | jev | prior (jev failed: ...)),
    # "prior"}, so runs with and without --jev can be compared decision by decision.
    prediction: dict | None = None


@dataclass
class Constraints:
    objective: Objective = Objective.COST
    max_cost_nu: float | None = None
    max_latency_ms: float | None = None
    max_quality_risk: float = 0.0
    latency_weight: float | None = None


@dataclass
class RateRatios:
    """A price card as ratios to the model's uncached input price (input = 1.0)."""
    model: str
    output_ratio: float
    cache_read_ratio: float
    cache_write_ratio: float
    cache_write_1h_ratio: float | None
    usd_per_nu: float                # the uncached input price per token, in USD
    input_ratio: float = 1.0


@dataclass
class PlanContext:
    """What the planner, gates and cost model may look at for one decision."""
    run_id: str
    agent: AgentNode
    intercept: Intercept
    window: list[Segment]
    window_max_tokens: int | None
    current_versions: dict[str, str]
    constraints: Constraints
    allowlist: dict[str, bool]
    rates: RateRatios
    remaining_turns: int
    cache: CacheState | None = None
