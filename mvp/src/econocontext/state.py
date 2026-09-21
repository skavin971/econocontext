"""Typed in-process context; persisted entities live in contracts.py."""

from typing import Any, TypedDict

from .adapters import DomainAdapter
from .contracts import Evidence, Limits, Result, Worker


class ExecutionState(TypedDict, total=False):
    run_id: str
    worker: Worker
    adapter: DomainAdapter
    limits: Limits
    planning_limits: Limits
    method: str
    fingerprint: str
    versions: dict[str, str]
    attempts: int
    known_cost: float
    start: float
    active_operation: str
    integration: tuple[str, str]
    last_attempt: str
    candidate_tokens: dict[str, int]
    candidate_prefixes: dict[str, str]
    prefix_tokens: dict[str, int]
    parent_tokens: int
    exact_tokens: dict[str, int]
    preview: bool
    history: list[dict[str, Any]]
    reuse_uncertain: bool
    plans: int
    pressure: bool


class CandidateMatches(TypedDict):
    required: list[str]
    direct: list[str]
    related: list[str]
    evidence: dict[str, Evidence]
    workers: list[Worker]
    results: list[Result]
    revision: str
