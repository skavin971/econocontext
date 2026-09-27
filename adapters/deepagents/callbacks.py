"""Measurement: every physical model call's reported usage, into the ledger.

Why it exists: a middleware sees only the calls its agent's loop makes; Deep Agents'
summarization calls the model from inside other middleware, and subagents run
their own loops. A LangChain callback handler sees every call, including those,
so the ledger never under-counts. It is installed in BOTH arms, so baseline and
EconoContext runs are measured with the same ruler.
What it must never do: decide anything, or break the agent. Its own errors are
swallowed and logged; only a budget stop is allowed to end a run.

Attribution: a call belongs to the subagent of the nearest `task` tool run above
it in LangChain's run tree (agent id '<run>:task:<tool_call_id>'), else the root.
"""

import logging
import threading
import time
from contextvars import ContextVar
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from econocontext.engine import EconoContext

log = logging.getLogger("econocontext")

# Set by the decision middleware around each model call, so the outcome row can be
# joined to the plan_prompt decision that shaped the request.
CURRENT_DECISION: ContextVar[str | None] = ContextVar("econocontext_decision", default=None)


class BudgetExceeded(RuntimeError):
    pass


class MeasurementCallback(BaseCallbackHandler):
    raise_error = True  # lets BudgetExceeded stop the run; everything else is caught

    def __init__(self, engine: EconoContext, root_id: str, usage_mapper, budget_usd=None,
                 spent_elsewhere_usd: float = 0.0, global_budget_usd=None):
        self.engine, self.root_id, self.map_usage = engine, root_id, usage_mapper
        self.budget, self.global_budget = budget_usd, global_budget_usd
        self.spent_elsewhere = spent_elsewhere_usd  # earlier runs in this comparison
        self.lock = threading.Lock()
        self.runs: dict[UUID, tuple[UUID | None, str, str | None, str | None]] = {}
        self.open: dict[UUID, tuple[str, str, str | None, float]] = {}

    def _agent_for(self, parent: UUID | None) -> str:
        seen = 0
        while parent is not None and parent in self.runs and seen < 10_000:
            up, kind, name, call_id = self.runs[parent]
            if kind == "tool" and name == "task" and call_id:
                return f"{self.engine.run_id}:task:{call_id}"
            parent, seen = up, seen + 1
        return self.root_id

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kw):
        with self.lock:
            self.runs[run_id] = (parent_run_id, "chain", kw.get("name"), None)

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, inputs=None,
                      **kw):
        name = (serialized or {}).get("name") or kw.get("name")
        call_id = kw.get("tool_call_id")
        with self.lock:
            self.runs[run_id] = (parent_run_id, "tool", name, call_id)
            parent_agent = self._agent_for(parent_run_id)
        if name == "task" and call_id:
            try:  # register the subagent in the tree (both arms)
                self.engine.registry.ensure_agent(f"{self.engine.run_id}:task:{call_id}",
                                                  parent_agent, (inputs or {}).get("subagent_type"))
            except Exception:
                log.exception("econocontext: could not register subagent")

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None,
                            metadata=None, **kw):
        with self.lock:
            self.runs[run_id] = (parent_run_id, "model", None, None)
            agent = self._agent_for(parent_run_id)
        phase = "compaction" if (metadata or {}).get("lc_source") == "summarization" else "agent"
        self._check_budget()  # before the call is sent: a refused call costs nothing
        with self.lock:
            self.open[run_id] = (agent, phase, CURRENT_DECISION.get(), time.perf_counter())

    def _check_budget(self) -> None:
        spent = self.engine.ledger.run_usd(self.engine.run_id)
        if self.budget is not None and spent >= self.budget:
            raise BudgetExceeded(f"instance budget reached: ${spent:.4f} >= ${self.budget:.2f}")
        if self.global_budget is not None and self.spent_elsewhere + spent >= self.global_budget:
            raise BudgetExceeded(f"live-run budget reached: ${self.spent_elsewhere + spent:.4f}")

    def on_llm_end(self, response, *, run_id, **kw):
        try:
            with self.lock:
                entry = self.open.pop(run_id, None)
            if entry is None:
                return  # a replayed end for a call already recorded
            agent, phase, decision_id, started = entry
            message = getattr(response.generations[0][0], "message", None)
            usage = self.map_usage(getattr(message, "usage_metadata", None),
                                   (time.perf_counter() - started) * 1000)
            self.engine.record(agent, decision_id, usage, outcome_id=str(run_id), phase=phase)
        except Exception:
            log.exception("econocontext: failed to record a model call")

    def on_llm_error(self, error, *, run_id, **kw):
        try:
            with self.lock:
                entry = self.open.pop(run_id, None)
            if entry is None:
                return
            agent, phase, decision_id, started = entry
            usage = self.map_usage(None, (time.perf_counter() - started) * 1000)
            usage.raw = {"error": f"{type(error).__name__}: {str(error)[:300]}"}
            # Usage unknown: recorded with NULL counters, so the cost is marked incomplete.
            self.engine.record(agent, decision_id, usage, outcome_id=str(run_id), phase=phase)
        except Exception:
            log.exception("econocontext: failed to record a failed model call")
