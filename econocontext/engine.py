"""The EconoContext facade: the intercept API a host adapter calls.

Why it exists: one small surface for every host. Each intercept runs the same
pipeline: monitor (record what is) -> planner (propose) -> optimizer (gate,
price, choose) -> assembler (render) -> guard (validate, fail open) -> host.
What it must never do: run the host's work itself, or let its own failure change
what the host would have done. Every decision is logged; in observe mode none is
applied.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from . import config as config_module
from .assembler.assembler import pointer_text, render
from .costmodel.meter import ExplicitCache, ImplicitCache
from .costmodel.options import Shape
from .planner.handoff import brief as handoff_brief
from .guard.fail_open import guarded
from .guard.validate import problems
from .host import Host, HostCapabilities
from .monitor.cache_belief import CacheBelief
from .pricing.ledger import Ledger, summary
from .monitor.registry import Registry
from .optimizer.optimizer import select
from .planner import jev_planner, planner
from .pricing.predictor import remaining_turns
from .pricing.rates import ratios
from .store import retrieval
from .store.db import AgentDB
from .tokens import count_tokens
from .types import (AdmitResult, Candidate, CostBreakdown, Decision, DispatchIntent,
                    DispatchOutcome, DispatchResult, HostRequest, Intercept, Mode, OperatorType,
                    PlanContext, ProviderUsage, RenderedRequest, Segment, SegmentKind, ToolAction,
                    ToolCallEvent, ToolResultEvent)


@dataclass(frozen=True)
class Placement:
    """Where delegated work runs instead of the host's own new worker."""
    arm: str              # RESUME | HANDOFF
    title: str | None     # RESUME: the worker to continue
    worker_id: str        # the idle worker it continues, or whose lines it hands off
    brief: str            # HANDOFF: the text to add to the task


class EconoContext:
    def __init__(self, config_dir: str, host: Host | None, run_id: str, *, host_name: str,
                 arm: str, instance_id: str | None = None, db_path: str | None = None,
                 mode: str | None = None, jev: bool = False, overrides: dict | None = None,
                 workdir: str | None = None, history=None):
        self.config = config_module.load(config_dir, overrides)
        # learn/predictors.History: learned H_hat and p_hat instead of the config guesses.
        self.history = history
        # --jev: ask planner/jev_planner.py for p_need_again instead of the fixed guess.
        self.jev = jev
        self.cfg = self.config.raw
        self.mode = Mode(mode) if mode in ("observe", "autopilot") else self.config.mode
        self.host = host
        self.host_name = host_name
        self.workdir = workdir
        self.caps = host.capabilities if host else HostCapabilities()
        self.run_id = run_id
        self.arm = arm
        self.db = AgentDB(db_path or self.cfg["storage"]["db_path"])
        self.db.start_run(run_id, host_name, instance_id, arm, mode or self.mode.value,
                          self.cfg["model"]["name"], self.cfg["model"]["temperature"],
                          self.config.fingerprint, jev=jev, overrides=overrides, workdir=workdir)
        self.registry = Registry(self.db, run_id)
        self.belief = CacheBelief(self.cfg["cache"]["ttl_seconds"],
                                  self.config.card.min_cacheable_tokens)
        self.ledger = Ledger(self.db, self.config.card, self.config.cards)
        self.predicted_cache: dict[str, int] = {}  # decision id -> predicted cached tokens
        self.deadline = self.cfg["guard"]["decision_deadline_ms"]

    # -- shared plumbing -------------------------------------------------------------

    def _context(self, agent_id: str, intercept: Intercept, window: list[Segment]) -> PlanContext:
        agent = self.registry.ensure_agent(agent_id)
        today = datetime.now(timezone.utc).date()
        return PlanContext(
            run_id=self.run_id, agent=agent, intercept=intercept, window=window,
            window_max_tokens=self.cfg["limits"]["window_max_tokens"],
            current_versions=self.db.current_versions(self.run_id),
            constraints=self.config.constraints, allowlist=self._allowlist(),
            rates=ratios(self.config.card, today, sum(s.tokens for s in window) or None),
            remaining_turns=self._remaining(agent_id, agent),
            cache=self.belief.state(agent_id), resident_rate=self._resident_rate())

    def _remaining(self, agent_id: str, agent) -> int:
        """H: model calls this agent will still make. Learned (H_hat) when a History is
        given, else the config guess."""
        if self.history is None:
            return remaining_turns(agent, self.cfg["predictor"]["remaining_turns_default"])
        return self.history.h_hat(agent_id, self.db.calls_so_far(agent_id))

    def _resident_rate(self) -> float:
        """Learned mode: price residency at this run's observed cache mix (earlier runs'
        until this run has calls). Otherwise 1.0, as uncached."""
        if self.history is None:
            return 1.0
        share = self.db.cache_share(self.run_id)
        share = self.history.cache_share if share is None else share
        read = ratios(self.config.card, datetime.now(timezone.utc).date()).cache_read_ratio
        return (1 - share) + share * read

    def _p_of(self, tool: str, age: int) -> float:
        if self.history is None:
            return self.cfg["predictor"]["kind_need_again"]["tool_result"]
        return self.history.p_hat(tool, age)[0]

    def _pointer_len(self, segment: Segment) -> int:
        return count_tokens(pointer_text(segment, "<path>", self.cfg["planner"]["pointer_preview_lines"]))

    def _allowlist(self) -> dict[str, bool]:
        allow = dict(self.cfg["allowlist"])
        # A capability the host lacks removes the operator, whatever the config says.
        allow["POINTER"] = allow.get("POINTER", False) and self.caps.pointer
        allow["ANSWER_FROM_STORE"] = allow.get("ANSWER_FROM_STORE", False) and self.caps.answer_from_store
        allow["REUSE_RESULT"] = allow.get("REUSE_RESULT", False) and self.caps.reuse_result
        allow["RESUME"] = allow.get("RESUME", False) and self.caps.resume
        allow["COMMIT_PENDING"] = allow.get("COMMIT_PENDING", False) and self.caps.pointer
        for name in ("ZONED", "COMMIT_PENDING", "RETRIEVE_FROM_STORE"):
            allow[name] = allow.get(name, False) and self.caps.edit_request
        return allow

    def _apply(self, decision: Decision) -> bool:
        return self.mode == Mode.AUTOPILOT and not decision.chosen.is_host_default

    def _log(self, agent_id, decision: Decision | None, intercept: Intercept, ms, error=None,
             cache_predicted=None, manifest_hash=None) -> str:
        if decision is None:  # failed open before a decision existed: record that
            decision = Decision(id=hashlib.sha256(f"{self.run_id}{agent_id}{ms}".encode())
                                .hexdigest()[:32], intercept=intercept,
                                chosen=Candidate("HOST_DEFAULT", OperatorType.EXACT,
                                                 is_host_default=True),
                                cost=CostBreakdown(), rejected=[], feasible=[],
                                candidate_costs={})
        self.db.add_decision(self.run_id, agent_id, decision, self.mode.value, ms, error,
                             cache_predicted, manifest_hash)
        return decision.id

    # -- runtime timing -------------------------------------------------------------

    def start_span(self, span_id: str, agent_id: str, kind: str, name: str,
                   native_id: str | None = None, decision_id: str | None = None,
                   metadata: dict | None = None) -> None:
        self.registry.activity_start(agent_id)
        try:
            self.db.add_runtime_span(span_id, self.run_id, agent_id, kind, name, native_id,
                                     decision_id, metadata)
        except Exception:
            self.registry.activity_end(agent_id)
            raise

    def finish_span(self, span_id: str, agent_id: str, duration_ms: float,
                    status: str = "completed", metadata: dict | None = None) -> None:
        try:
            self.db.finish_runtime_span(span_id, duration_ms, status, metadata)
        finally:
            self.registry.activity_end(agent_id)

    # -- intercepts -----------------------------------------------------------------

    def plan_prompt(self, agent_id: str, request: HostRequest) -> RenderedRequest:
        """Before every model call. Returns the request to send (the host's own, unless applied)."""
        fingerprint = self.config.fingerprint
        box: dict = {}

        def decide() -> RenderedRequest:
            window = self.registry.sync_window(agent_id, request.segments)
            predicted = self.belief.predict(agent_id, window)
            retrieved = []
            if self._allowlist().get("RETRIEVE_FROM_STORE"):
                newest = window[-1].text if window else ""
                retrieved = retrieval.search(self.db, self.run_id, newest, agent_id,
                                             limit=self.cfg["planner"]["retrieve_limit"])
            ctx = self._context(agent_id, Intercept.PLAN_PROMPT, window)
            edits = []
            if self._allowlist().get("COMMIT_PENDING"):
                arrival = {s.id: self.db.call_count_at(agent_id, s.id) for s in window
                           if s.kind == SegmentKind.TOOL_RESULT}
                edits = planner.stale_edits(ctx, window, arrival, self.db.calls_so_far(agent_id),
                                            self._p_of, self._pointer_len)
            candidates = planner.for_prompt(ctx, self.cfg, retrieved, edits, bool(predicted))
            decision = select(candidates, ctx, self.config.constraints, self.cfg)
            pointer_ids = set(decision.payloads.get("COMMIT_PENDING", {}).get("segment_ids", []))
            rendered = render(window, decision.chosen.name, fingerprint, retrieved,
                              pointer_ids=pointer_ids)
            issues = problems(rendered, window, ctx.window_max_tokens)
            if issues or not self._apply(decision):
                rendered = render(window, "AS_IS", fingerprint, applied=False)
                if issues:
                    box["error"] = "invalid plan: " + "; ".join(issues)
            else:
                rendered.applied = decision.applied = True
                if decision.chosen.name == "COMMIT_PENDING" and self.host:
                    lines = self.cfg["planner"]["pointer_preview_lines"]
                    rendered.pointer_texts = {
                        s.id: pointer_text(s, self.host.pointer_store.materialize(s), lines)
                        for s in window if s.id in pointer_ids}
            box.update(decision=decision, predicted=predicted)
            return rendered

        def default() -> RenderedRequest:
            return render(request.segments, "AS_IS", fingerprint, applied=False)

        rendered, error, ms = guarded(decide, default, self.deadline)
        decision = box.get("decision")
        rendered.decision_id = self._log(agent_id, decision, Intercept.PLAN_PROMPT, ms,
                                         error or box.get("error"), box.get("predicted"),
                                         rendered.manifest.hash)
        self.predicted_cache[rendered.decision_id] = box.get("predicted")
        self.belief.sent(agent_id, rendered.segments)
        return rendered

    def before_tool_call(self, agent_id: str, event: ToolCallEvent) -> ToolAction:
        """Run the tool, or answer it byte-identically from the store."""
        box: dict = {}

        def decide() -> ToolAction:
            stored = None
            if not event.side_effect:
                row = self.db.find_tool_result(self.run_id, event.tool_name, event.args_key)
                stored = dict(row) if row else None
            ctx = self._context(agent_id, Intercept.BEFORE_TOOL_CALL, self.registry.window(agent_id))
            decision = select(planner.for_tool_call(ctx, self.cfg, event, stored), ctx,
                              self.config.constraints, self.cfg)
            decision.subject_id = event.args_key
            box["decision"] = decision
            if decision.chosen.name == "ANSWER_FROM_STORE" and self._apply(decision):
                decision.applied = True
                return ToolAction(run_tool=False, stored_text=stored["text"],
                                  tool_result_id=stored["tool_result_id"])
            return ToolAction(run_tool=True)

        action, error, ms = guarded(decide, lambda: ToolAction(run_tool=True), self.deadline)
        action.decision_id = self._log(agent_id, box.get("decision"), Intercept.BEFORE_TOOL_CALL,
                                       ms, error)
        return action

    def admit_tool_result(self, agent_id: str, event: ToolResultEvent) -> AdmitResult:
        """Store a tool result and decide what enters the window: the full text or a pointer."""
        current = self.db.current_versions(self.run_id)
        read_set = {src: current.get(src, "0") for src in event.reads}
        segment = make_segment(self.run_id, agent_id, event.call_id, SegmentKind.TOOL_RESULT,
                               event.text, role="tool", source=event.source,
                               version=read_set.get(event.source or "", None),
                               pair_id=event.call_id, needs_exact_bytes=event.needs_exact_bytes)
        box: dict = {}

        def decide() -> AdmitResult:
            self.registry.add_segment(segment)
            self.db.add_tool_result(segment.id, self.run_id, agent_id, event.tool_name,
                                    event.args_key, segment.id, read_set, event.side_effect)
            self.registry.record_read(agent_id, read_set)
            if event.side_effect:
                self.registry.mark_side_effect(agent_id)
            preview = pointer_text(segment, "<path>", self.cfg["planner"]["pointer_preview_lines"])
            ctx = self._context(agent_id, Intercept.ADMIT_TOOL_RESULT, self.registry.window(agent_id))
            prediction = self._predict(segment, event, ctx)
            decision = select(planner.for_tool_result(ctx, self.cfg, segment, count_tokens(preview),
                                                      prediction["p_need_again"]),
                              ctx, self.config.constraints, self.cfg)
            decision.prediction = prediction
            decision.subject_id = segment.id
            box["decision"] = decision
            if decision.chosen.name == "POINTER" and self._apply(decision) and self.host:
                locator = self.host.pointer_store.materialize(segment)
                decision.applied = True
                return AdmitResult(segment, pointer_text(
                    segment, locator, self.cfg["planner"]["pointer_preview_lines"]))
            return AdmitResult(segment, segment.text)

        result, error, ms = guarded(decide, lambda: AdmitResult(segment, event.text), self.deadline)
        result.decision_id = self._log(agent_id, box.get("decision"), Intercept.ADMIT_TOOL_RESULT,
                                       ms, error)
        return result

    def _predict(self, segment: Segment, event: ToolResultEvent, ctx: PlanContext) -> dict:
        """p_need_again for a tool result: the fixed guess, or Jev when the run uses --jev.

        Both numbers are logged (decisions.prediction), so the two tracks can be compared.
        If Jev fails or is not built yet, the fixed guess is used and the reason recorded.
        """
        prior = planner.prior_p_need_again(segment, self.cfg)
        if self.history is not None and not self.jev:
            p, source = self.history.p_hat(event.tool_name, age=0)
            return dict(p_need_again=p, source=source, prior=prior, h=ctx.remaining_turns)
        if not self.jev:
            return dict(p_need_again=prior, source="prior", prior=prior)
        try:
            p = float(jev_planner.p_need_again(segment, event, ctx, self.cfg, prior))
            if not 0.0 <= p <= 1.0:
                raise ValueError(f"Jev returned {p}, not a probability")
            return dict(p_need_again=p, source="jev", prior=prior)
        except Exception as exc:  # fail open: the agent never stops because of Jev
            return dict(p_need_again=prior, source=f"prior (jev failed: {str(exc)[:200]})",
                        prior=prior)

    def plan_placement(self, agent_id: str, task: str, worker_type: str, workers: list[dict],
                       file_tokens: dict[str, int], need_named_files: bool = True,
                       handoff: bool = False) -> tuple[str, Placement | None]:
        """Before a sub-task is delegated: a new worker, an existing one (RESUME), or a new
        one given an idle worker's lines (HANDOFF, when the host can carry it out).
        Returns (decision id, what to do instead of the host's own new worker, or None)."""
        box: dict = {}

        def decide() -> Placement | None:
            ctx = self._context(agent_id, Intercept.PLAN_DISPATCH, self.registry.window(agent_id))
            calls_hat = (self.history.h_hat(f"{self.run_id}:{worker_type}:new", 0) if self.history
                         else self.cfg["cost_model"]["fresh_expected_calls"])
            shape, cache = self._worker_model()
            briefs = {}
            if handoff and self.workdir:
                limit = self.cfg.get("placement", {}).get("handoff_max_tokens", 4000)
                for w in workers:  # a busy worker's lines so far can be handed off too
                    text = handoff_brief(self.workdir, w.get("lines") or {}, task, limit)
                    if text:
                        briefs[w["worker_id"]] = text
            decision = select(planner.for_placement(ctx, self.cfg, task, workers, calls_hat,
                                                    file_tokens, shape, cache, need_named_files,
                                                    {k: count_tokens(v) for k, v in briefs.items()}),
                              ctx, self.config.constraints, self.cfg,
                              force=self.cfg.get("placement", {}).get("force"))
            decision.subject_id = hashlib.sha256(f"{worker_type}|{task}".encode()).hexdigest()
            box["decision"] = decision
            chosen = decision.chosen
            if chosen.name in ("RESUME", "HANDOFF") and self._apply(decision):
                decision.applied = True
                return Placement(chosen.name, chosen.payload.get("title"), chosen.payload["worker_id"],
                                 briefs.get(chosen.payload["worker_id"], ""))
            return None

        placement, error, ms = guarded(decide, lambda: None, self.deadline)
        return self._log(agent_id, box.get("decision"), Intercept.PLAN_DISPATCH, ms, error), placement

    def _worker_model(self) -> tuple[Shape, object]:
        """This harness's worker loops, and its provider's cache (Anthropic: explicit)."""
        shapes = self.cfg["cost_model"]["worker_shapes"]
        shape = Shape(**shapes.get(self.host_name, shapes["default"]))
        cache = (ExplicitCache() if self.config.card.provider == "anthropic"
                 else ImplicitCache(**self.cfg["cost_model"]["implicit_cache"]))
        return shape, cache

    def plan_dispatch(self, intent: DispatchIntent,
                      run_default: Callable[[], DispatchResult]) -> DispatchOutcome:
        """Before delegated work: reuse a byte-identical stored result, or run the host's own."""
        box: dict = {}

        def decide() -> tuple[Decision, dict | None]:
            row = self.db.find_stored_result(self.run_id, intent.task_key)
            stored = dict(row) if row else None
            ctx = self._context(intent.agent_id, Intercept.PLAN_DISPATCH,
                                self.registry.window(intent.agent_id))
            decision = select(planner.for_dispatch(ctx, self.cfg, intent, stored), ctx,
                              self.config.constraints, self.cfg)
            decision.subject_id = intent.task_key
            box["decision"] = decision
            return decision, stored

        (decision, stored), error, ms = guarded(decide, lambda: (None, None), self.deadline)
        if decision is not None and decision.chosen.name == "REUSE_RESULT" and self._apply(decision):
            decision.applied = True
            decision_id = self._log(intent.agent_id, decision, Intercept.PLAN_DISPATCH, ms, error)
            return DispatchOutcome(stored["result_text"], True, stored["result_id"], decision_id)
        decision_id = self._log(intent.agent_id, decision, Intercept.PLAN_DISPATCH, ms, error)
        # The host's own delegation runs outside the guard: its failures are the host's.
        result = run_default()
        sub = self.registry.ensure_agent(result.subagent_id)
        self.db.add_stored_result(hashlib.sha256(f"{self.run_id}{intent.call_id}".encode())
                                  .hexdigest()[:32], self.run_id, intent.task_key,
                                  result.subagent_id, result.result_text, dict(sub.read_set),
                                  sub.side_effect)
        return DispatchOutcome(result.result_text, False, None, decision_id)

    # -- measurement and lifecycle ------------------------------------------------------

    def record(self, agent_id: str, decision_id: str | None, usage: ProviderUsage,
               outcome_id: str, phase: str = "agent", model: str | None = None) -> float:
        """After every model call: exact cost from reported usage, priced by the card of
        `model` (the model the call used; default the run's). Returns USD."""
        self.registry.ensure_agent(agent_id)
        self.belief.correct(agent_id, self.predicted_cache.get(decision_id or ""), usage.cache_read)
        return self.ledger.record(outcome_id, self.run_id, agent_id, decision_id, phase, usage,
                                  model=model)

    def on_file_write(self, agent_id: str, path: str | None) -> None:
        """Write barrier: bump the path (and the workspace epoch); invalidate what depended on it."""
        self.registry.mark_side_effect(agent_id)
        self.db.bump(self.run_id, [path] if path else [])

    def observe_evidence(self, agent_id: str, call_no: int, events: list) -> None:
        """Evidence entering an agent's context, and sources changing (evidence.py). Measured
        only: nothing here changes what the agent sees."""
        if events:
            self.db.add_evidence_events(self.run_id, agent_id, call_no, events)

    def on_turn_end(self, agent_id: str) -> None:
        self.registry.turn_end(agent_id)

    def end_run(self, status: str) -> None:
        self.db.end_run(self.run_id, status)

    def report(self, run_id: str | None = None) -> dict:
        return summary(self.db, run_id or self.run_id)


def make_segment(run_id: str, agent_id: str, native_id: str, kind: SegmentKind, text: str, *,
                 role: str, source: str | None = None, version: str | None = None,
                 pair_id: str | None = None, pinned: bool = False,
                 needs_exact_bytes: bool = False) -> Segment:
    """Build a segment with its identity: sha256(agent_id | native_id | content_hash)."""
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    seg_id = hashlib.sha256(f"{agent_id}|{native_id}|{content_hash}".encode()).hexdigest()
    return Segment(id=seg_id, run_id=run_id, agent_id=agent_id, native_id=native_id, kind=kind,
                   text=text, tokens=count_tokens(text), content_hash=content_hash, source=source,
                   version=version, pinned=pinned, needs_exact_bytes=needs_exact_bytes,
                   pair_id=pair_id, role=role)
