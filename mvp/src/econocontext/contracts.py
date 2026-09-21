from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def uid() -> str:
    return uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Mode(StrEnum):
    REUSE = "REUSE"
    CONTINUE = "CONTINUE"
    FRESH = "FRESH"


class Status(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET = "budget-exceeded"
    INTERRUPTED = "interrupted"


class Limits(Record):
    max_attempts: int = Field(32, ge=1, le=1000)
    output_tokens: int = Field(1024, ge=64)
    context_tokens: int = Field(16384, ge=256)
    focused_tokens: int = Field(2048, ge=1)
    broader_tokens: int = Field(4096, ge=1)
    max_children: int = Field(2, ge=0, le=8)
    tool_timeout: float = Field(30, gt=0)
    deadline: float = Field(300, gt=0)
    max_cost: float | None = Field(None, ge=0)
    latency: float = Field(120, gt=0)
    safety_margin: float = Field(1.2, ge=1)
    retries: int = Field(1, ge=0, le=3)
    # An observation smaller than the finding that would replace it is not worth
    # an operation; delegating it would grow the parent rather than bound it.
    observation_tokens: int = Field(512, ge=0)
    plan_pressure: float = Field(0.0, ge=0, le=1)
    max_plans: int = Field(64, ge=0)


class Operation(Record):
    created: str = Field(default_factory=now)
    ended: str | None = None
    id: str = Field(default_factory=uid)
    run_id: str
    worker_id: str
    kind: str = "analysis"
    goal: str
    scope: str
    arguments: dict = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)
    bindings: dict[str, str] = Field(default_factory=dict)
    output_contract: str = "brief finding with evidence references"
    completion: str = "structured finding supplied"
    reusable: bool = True
    status: str = "pending"
    selected_plan: str | None = None
    preceding_operation: str | None = None
    result_tokens: int = Field(512, ge=32, le=2048)
    # How this operation was identified. Execution provenance, deliberately not
    # part of key(): the same work must reuse whoever raised it.
    origin: Literal["request", "observation"] = "request"

    def key(self, fingerprint: str) -> str:
        return digest(
            [
                self.kind,
                self.goal,
                self.scope,
                self.arguments,
                self.output_contract,
                self.bindings,
                fingerprint,
                "prompt-v2",
            ]
        )


class Worker(Record):
    created: str = Field(default_factory=now)
    id: str = Field(default_factory=uid)
    run_id: str
    parent_id: str | None = None
    role: Literal["root", "child"] = "root"
    scope: str
    status: str = "idle"
    revision: int = 0
    bindings: dict[str, str] = Field(default_factory=dict)
    fingerprint: str
    context_manifest: str | None = None
    cache_observation: dict | None = None


class Evidence(Record):
    created: str = Field(default_factory=now)
    id: str = Field(default_factory=uid)
    run_id: str
    source: str
    version: str
    payload: str
    kind: str = "text"
    excerpt: tuple[int, int] | None = None
    attempt_id: str | None = None
    tokens: int
    description: str


class Result(Record):
    created: str = Field(default_factory=now)
    id: str = Field(default_factory=uid)
    run_id: str
    operation_key: str
    worker_id: str
    attempt_id: str | None = None
    payload: str
    evidence: list[str]
    requirements: dict[str, str]
    fingerprint: str
    verification: str = "unverified"
    reusable: bool = True


class Estimate(Record):
    cost: float
    latency: float
    breakdown: dict[str, float]
    uncertainty: float
    provenance: str
    basis: str
    tokens: int
    samples: int = 0


class CandidatePlan(Record):
    created: str = Field(default_factory=now)
    id: str = Field(default_factory=uid)
    operation_id: str
    mode: Mode
    worker_id: str | None = None
    view: Literal["FOCUSED", "BROADER"] | None = None
    evidence: list[str] = Field(default_factory=list)
    result_id: str | None = None
    state_revision: str
    estimate: Estimate | None = None
    rejections: list[str] = Field(default_factory=list)

    def label(self) -> str:
        return (
            f"{self.mode}:{self.view or ('result' if self.mode == Mode.REUSE else self.worker_id)}"
        )


class Assembled(Record):
    messages: list[dict]
    tools: list[dict]
    tokens: int
    manifest: str
    fingerprint: str
    request: dict


class FeasibilityError(Exception):
    pass


class StopRun(Exception):
    def __init__(self, status: Status, reason: str):
        self.status, self.reason = status, reason
        super().__init__(reason)


class ModelResponse(Record):
    message: dict
    usage: dict | None
    synthetic: bool = False


class Task(Record):
    adapter: Literal["coding", "ledger", "research"] = "coding"
    goal: str | None = None
    repository: str | None = None
    commit: str | None = None
    corpus: str | None = None
    instance_id: str | None = None


class RunRequest(Record):
    task: Task = Field(default_factory=Task)
    method: Literal["react", "econocontext"] = "econocontext"
    limits: Limits = Field(default_factory=Limits)
    idempotency_key: str | None = Field(None, max_length=200)


class RunAccepted(Record):
    run_id: str
    status: Status


class RunView(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    status: Status
    created: str
    started: str | None
    ended: str | None
    request: RunRequest
    fingerprint: str
    outcome: dict | None
    verification: str
    reason: str | None


class TracePage(Record):
    events: list[dict]
    next_cursor: int


class MetricsView(BaseModel):
    model_config = ConfigDict(extra="allow")
    run_id: str
    status: Status
    verification: str
    synthetic: bool
    attempts: int
    model_attempts: int
    tool_attempts: int
    known_cost: float
    cost_complete: bool
    usage_complete: bool
    tokens: dict[str, int]
    wall_seconds: float
    comparisons: list[dict]


class CancelView(Record):
    run_id: str
    status: Status
    cancellation: Literal["requested", "already-terminal"]


class HealthView(Record):
    status: str
    storage: str
    backend: str
