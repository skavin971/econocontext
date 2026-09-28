"""LangChain agent middleware -> EconoContext engine calls. Translation only.

Why it exists: Deep Agents exposes its extension points as agent middleware. This
class maps them to the engine's intercepts:
  wrap_model_call   -> plan_prompt (request side); record happens in callbacks.py
  wrap_tool_call    -> before_tool_call, admit_tool_result, on_file_write; plan_dispatch on `task`
  after_model       -> on_turn_end
One instance is installed on the root agent and on every subagent spec (declared
subagents do not inherit the root's middleware in deepagents 0.7.19).
What it must never do: decide anything, or make the agent fail. Any error in this
translation proceeds exactly as the host would have without EconoContext.

Which agent is calling: a ContextVar. The root's wrapper around the `task` tool
sets it to the subagent's id before running the native handler; the subagent's
own middleware reads it. Parallel tool calls run in copied contexts, so each sees
its own value.
"""

import hashlib
import json
import logging
import re
import time
from contextvars import ContextVar

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from econocontext.engine import EconoContext
from econocontext.types import DispatchIntent, DispatchResult, HostRequest, ToolCallEvent, ToolResultEvent

from .callbacks import CURRENT_DECISION
from .translate import from_segments, to_segments

log = logging.getLogger("econocontext")
CURRENT_AGENT: ContextVar[str | None] = ContextVar("econocontext_agent", default=None)

FILE_WRITES = {"write_file", "edit_file", "delete"}      # change one path
SHELL = {"execute"}                                        # may change anything
SEARCHES = {"ls", "glob", "grep"}                          # depend on the whole workspace


def args_key(name: str, args: dict) -> str:
    return hashlib.sha256(f"{name}|{json.dumps(args, sort_keys=True, default=str)}".encode()).hexdigest()


def task_key(subagent_type: str, description: str) -> str:
    normalized = re.sub(r"\s+", " ", description).strip()
    return hashlib.sha256(f"{subagent_type}|{normalized}".encode()).hexdigest()


def result_text(result) -> str | None:
    """The tool output a result carries (a ToolMessage, or a Command holding one)."""
    if isinstance(result, ToolMessage):
        return result.content if isinstance(result.content, str) else None
    if isinstance(result, Command) and isinstance(result.update, dict):
        for m in result.update.get("messages", []):
            if isinstance(m, ToolMessage) and isinstance(m.content, str):
                return m.content
    return None


def replace_text(result, text: str):
    if isinstance(result, ToolMessage):
        return result.model_copy(update={"content": text})
    if isinstance(result, Command) and isinstance(result.update, dict):
        msgs = [m.model_copy(update={"content": text}) if isinstance(m, ToolMessage) else m
                for m in result.update.get("messages", [])]
        return Command(update={**result.update, "messages": msgs})
    return result


class EconoMiddleware(AgentMiddleware):
    name = "EconoContextMiddleware"

    def __init__(self, engine: EconoContext, host, root_id: str):
        super().__init__()
        self.engine, self.host, self.root_id = engine, host, root_id

    def agent(self) -> str:
        return CURRENT_AGENT.get() or self.root_id

    # -- plan_prompt -------------------------------------------------------------------

    def _plan(self, request):
        agent = self.agent()
        try:
            segments, originals = to_segments(self.engine.run_id, agent, request.system_message,
                                              request.tools, request.messages)
            rendered = self.engine.plan_prompt(agent, HostRequest(agent, segments))
            if rendered.applied:
                request = request.override(messages=from_segments(rendered.segments, originals))
            return request, rendered.decision_id
        except Exception:
            log.exception("econocontext: plan_prompt translation failed; sending host request")
            return request, None

    def wrap_model_call(self, request, handler):
        request, decision_id = self._plan(request)
        token = CURRENT_DECISION.set(decision_id)
        try:
            return handler(request)
        finally:
            CURRENT_DECISION.reset(token)

    async def awrap_model_call(self, request, handler):
        request, decision_id = self._plan(request)
        token = CURRENT_DECISION.set(decision_id)
        try:
            return await handler(request)
        finally:
            CURRENT_DECISION.reset(token)

    def after_model(self, state, runtime):
        try:
            self.engine.on_turn_end(self.agent())
        except Exception:
            log.exception("econocontext: on_turn_end failed")
        return None

    # -- tools -----------------------------------------------------------------------------

    def wrap_tool_call(self, request, handler):
        call = request.tool_call
        if call["name"] == "task":
            return self._dispatch(request, handler)
        agent, name, args = self.agent(), call["name"], call.get("args") or {}
        side_effect = name in FILE_WRITES or name in SHELL
        try:
            action = self.engine.before_tool_call(agent, ToolCallEvent(
                agent, call["id"], name, args, args_key(name, args), side_effect))
            if not action.run_tool:
                # Byte-identical earlier output; the reuse is recorded only in the DB.
                return ToolMessage(content=action.stored_text, tool_call_id=call["id"], name=name)
        except Exception:
            log.exception("econocontext: before_tool_call failed; running the tool")
        span_id = f"{self.engine.run_id}:tool:{call['id']}"
        span_started = False
        started = time.perf_counter()
        try:
            self.engine.start_span(span_id, agent, "tool", name, native_id=call["id"],
                                   metadata={"side_effect": side_effect})
            span_started = True
        except Exception:
            log.exception("econocontext: failed to start tool timing span")
        try:
            result = handler(request)
        except Exception:
            if span_started:
                try:
                    self.engine.finish_span(span_id, agent,
                                            (time.perf_counter() - started) * 1000, "failed")
                except Exception:
                    log.exception("econocontext: failed to finish failed tool span")
            raise
        if span_started:
            try:
                self.engine.finish_span(span_id, agent,
                                        (time.perf_counter() - started) * 1000, "completed")
            except Exception:
                log.exception("econocontext: failed to finish tool timing span")
        try:
            return self._after_tool(agent, call, name, args, side_effect, result)
        except Exception:
            log.exception("econocontext: admit_tool_result failed; admitting the host result")
            return result

    def _after_tool(self, agent, call, name, args, side_effect, result):
        path = args.get("file_path") or args.get("path")
        if name in FILE_WRITES:
            self.engine.on_file_write(agent, path)
        elif name in SHELL:
            changed = self.host.changed_paths() if self.host else None
            if changed is None:
                self.engine.on_file_write(agent, None)  # fallback: bump the workspace epoch
            for changed_path in changed or []:
                self.engine.on_file_write(agent, changed_path)
        text = result_text(result)
        if text is None:
            return result
        reads = [path] if name == "read_file" and path else (["*"] if name in SEARCHES else [])
        admitted = self.engine.admit_tool_result(agent, ToolResultEvent(
            agent, call["id"], name, args_key(name, args), text,
            path if name == "read_file" else None, reads, side_effect))
        return result if admitted.rendered_text == text else replace_text(result,
                                                                          admitted.rendered_text)

    def _dispatch(self, request, handler):
        call, parent = request.tool_call, self.agent()
        args = call.get("args") or {}
        subagent_type = str(args.get("subagent_type", ""))
        child = f"{self.engine.run_id}:task:{call['id']}"
        box: dict = {}

        def run_native():
            box["started"] = True
            span_id = f"{self.engine.run_id}:dispatch:{call['id']}"
            span_started = False
            started = time.perf_counter()
            try:
                self.engine.start_span(span_id, parent, "dispatch", "task",
                                       native_id=call["id"],
                                       metadata={"subagent_id": child,
                                                 "subagent_type": subagent_type})
                span_started = True
            except Exception:
                log.exception("econocontext: failed to start dispatch timing span")
            token = CURRENT_AGENT.set(child)
            try:
                box["result"] = handler(request)
            except Exception:
                if span_started:
                    try:
                        self.engine.finish_span(span_id, parent,
                                                (time.perf_counter() - started) * 1000, "failed")
                    except Exception:
                        log.exception("econocontext: failed to finish failed dispatch span")
                raise
            finally:
                CURRENT_AGENT.reset(token)
            if span_started:
                try:
                    self.engine.finish_span(span_id, parent,
                                            (time.perf_counter() - started) * 1000, "completed")
                except Exception:
                    log.exception("econocontext: failed to finish dispatch timing span")
            return box["result"]

        def run_default() -> DispatchResult:
            self.engine.registry.ensure_agent(child, parent, subagent_type)
            return DispatchResult(result_text(run_native()) or "", child)

        try:
            outcome = self.engine.plan_dispatch(DispatchIntent(
                parent, call["id"], subagent_type, str(args.get("description", "")),
                task_key(subagent_type, str(args.get("description", "")))), run_default)
        except Exception:
            if "result" in box:
                return box["result"]  # the host's work already ran; keep its result
            if box.get("started"):
                raise  # the host's own delegation failed: that failure is the host's
            log.exception("econocontext: plan_dispatch failed; delegating natively")
            return run_native()
        if outcome.reused:
            return ToolMessage(content=outcome.result_text, tool_call_id=call["id"], name="task")
        return box["result"]
