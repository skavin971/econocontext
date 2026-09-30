"""The tool tap: an Omnigent policy that sends every tool call and result to EconoContext.

Why it exists: tool results are where context grows and where reuse is safe or not.
Omnigent evaluates this policy for every tool call and result of the session tree, and
lets a policy replace a tool result before the model sees it (checked 2026-09-28,
question 1). This is the only file that knows Omnigent's event fields.

Attach it in the agent spec (the bench does this for the econo arm only):

    policies:
      econocontext:
        type: function
        handler: omnigent_layer.policy.econocontext
        factory_params: {run_id: <run id>, workdir: <absolute work directory>}

Omnigent's policy events carry no session id, and a sub-agent's own policies are not
evaluated (its events reach the root agent's policy), so events are attributed to the
run's root agent. Per-agent attribution comes from the gateway URL instead.

Dispatches (sys_session_send) are timed as `dispatch` spans and placed by the planner:
FRESH (as the root asked) or RESUME (an idle worker that already holds the task's files).
Omnigent applies a replaced tool call's arguments (checked 2026-09-29), so RESUME is
carried out by rewriting the dispatch's title to that worker's. Claude Code's own
sub-agents (the Agent tool) are placed the same way; there RESUME is carried out by
denying the new Agent call with a reason naming the idle worker to continue with
SendMessage (Omnigent honors only a deny on Claude Code's tool calls).

What it must never do: deny or delay a tool, or fail one. Every error abstains, and
Omnigent then does exactly what it would have done.
"""

import hashlib
import json
import logging
import os
import time

from econocontext.monitor import context_map
from econocontext.types import ToolCallEvent, ToolResultEvent

from . import agent_id, claude_workers, engine_for
from .workspace import Workspace

log = logging.getLogger("econocontext.policy")

# Omnigent 0.15.0 OS tools, Claude Code's own tools (claude-native reports them by their
# Claude names), and the benchmark's container shell. Reads name their path.
READS = {"sys_os_read": "path", "Read": "file_path"}
WRITES = {"sys_os_write", "sys_os_edit", "sys_os_shell", "testbed_shell",
          "Edit", "MultiEdit", "Write", "NotebookEdit", "Bash", "mcp__omnigent__testbed_shell"}
DISPATCH = "sys_session_send"  # the root sends a sub-task to a worker: {agent, args, title}
CLAUDE_AGENT = "Agent"  # Claude Code starts a sub-agent: {description, prompt, subagent_type, ...}


def args_key(name: str, args: dict) -> str:
    return hashlib.sha256(json.dumps([name, args], sort_keys=True, default=str).encode()).hexdigest()


def econocontext(run_id: str, workdir: str | None = None, agent: str = "root"):
    """Factory: one evaluator per agent spec. Omnigent calls it with factory_params."""
    workspace = Workspace(workdir) if workdir else None
    counter = iter(range(10**9))
    me = agent_id(run_id, agent)

    def relative(path: str) -> str:
        if not workdir:
            return path
        return os.path.relpath(os.path.join(workdir, path), workdir)

    def on_tool_call(engine, name: str, args: dict) -> None:
        if workspace is not None and workspace.known is None:
            workspace.snapshot()  # the baseline the first write is compared with
        engine.before_tool_call(me, ToolCallEvent(me, f"{name}:call", name, args,
                                                  args_key(name, args), name in WRITES))

    open_dispatches: list[tuple[str, float]] = []

    def on_dispatch(engine, args: dict) -> dict | None:
        """Time the dispatch, and choose the worker: a new one, or one that already holds
        the files (RESUME). In autopilot RESUME rewrites the title, and Omnigent continues
        that worker."""
        task, title = str(args.get("args") or ""), str(args.get("title") or "")
        span_id = f"{run_id}:dispatch:{next(counter)}"
        engine.start_span(span_id, me, "dispatch", title or "dispatch",
                          metadata={"title": title, "task": task, "worker": args.get("agent")})
        open_dispatches.append((span_id, time.monotonic()))
        workers = context_map.workers(engine.db, run_id, engine.cfg["cache"]["ttl_seconds"])
        _, resume_title = engine.plan_placement(me, task, str(args.get("agent") or "worker"),
                                                workers, context_map.file_tokens(engine.db, run_id))
        if resume_title and resume_title != title:
            return {"result": "ALLOW", "data": {**args, "title": resume_title}}
        return None

    def on_claude_agent(engine, args: dict) -> dict | None:
        """Claude Code decided to delegate `prompt` to a new sub-agent. EconoContext may only
        choose where that already-decided work runs: a new worker (let the call through),
        or an idle one that already worked in this run (RESUME). In autopilot RESUME is
        carried out by denying the Agent call with a reason naming the worker to continue
        with SendMessage; Claude Code then sends the same task there. Forks inherit the
        whole conversation and are never redirected."""
        if args.get("subagent_type") == "fork" or not args.get("prompt"):
            return None
        kind = args.get("subagent_type") or "general-purpose"
        workers = [w for w in claude_workers.workers(engine.db, run_id, engine.cfg["cache"]["ttl_seconds"])
                   if w["type"] == kind]
        sizes = {f: t for w in workers for f, t in w["file_tokens"].items()}
        _, resume = engine.plan_placement(me, args["prompt"], kind, workers, sizes,
                                          need_named_files=False)
        if not resume:
            return None
        worker = next(w for w in workers if w["worker_id"] == resume)
        held = ", ".join(worker["files"][:8]) or "its earlier findings"
        return {"result": "DENY", "reason": (
            f"EconoContext placement: do not start a new {kind} agent for this. Agent {resume} "
            f"is idle and already worked in this repository (it has read {held}); continuing it "
            f"reuses its context. Send the same task to it instead: SendMessage with "
            f"to: '{resume}', summary: '{(args.get('description') or 'next task')[:60]}', and "
            f"message: your prompt, unchanged.")}

    def on_tool_result(engine, name: str, args: dict, text: str) -> dict | None:
        path = args.get(READS[name]) if name in READS else None
        source = relative(path) if isinstance(path, str) else None
        event = ToolResultEvent(me, f"{name}:{next(counter)}", name, args_key(name, args), text,
                                source, [source] if source else [], name in WRITES)
        admitted = engine.admit_tool_result(me, event)
        if name in WRITES and workspace is not None:
            changed = workspace.changed()
            for changed_path in (changed if changed is not None else [None]):
                engine.on_file_write(me, changed_path)  # None: bump the workspace epoch only
        if admitted.rendered_text != text:  # POINTER was applied: the model sees the pointer
            return {"result": "ALLOW", "data": admitted.rendered_text}
        return None

    def evaluate(event: dict) -> dict | None:
        try:
            found = engine_for(run_id)
            if found is None or found[1] != "econo":
                return None
            engine, phase = found[0], event.get("type")
            name, data = event.get("target") or "", event.get("data") or {}
            if phase == "tool_call" and name == DISPATCH:
                return on_dispatch(engine, data.get("arguments") or {})
            if phase == "tool_call" and name == CLAUDE_AGENT:
                return on_claude_agent(engine, data.get("arguments") or {})
            if phase == "tool_result" and name == DISPATCH:
                if open_dispatches:
                    span_id, started = open_dispatches.pop(0)
                    engine.finish_span(span_id, me, (time.monotonic() - started) * 1000)
                return None
            if phase == "tool_call":
                on_tool_call(engine, name, data.get("arguments") or {})
            elif phase == "tool_result" and isinstance(data.get("result"), str):
                call = event.get("request_data") or {}
                return on_tool_result(engine, name, call.get("arguments") or {}, data["result"])
        except Exception:
            log.exception("econocontext policy failed open")
        return None

    return evaluate


POLICY_REGISTRY = [{
    "handler": "omnigent_layer.policy.econocontext",
    "kind": "factory",
    "name": "EconoContext",
    "description": "Logs every tool call and result to the EconoContext Agent DB and, "
                   "in autopilot, applies exact context decisions.",
    "params_schema": {
        "type": "object",
        "properties": {"run_id": {"type": "string"}, "workdir": {"type": "string"},
                       "agent": {"type": "string"}},
        "required": ["run_id"],
    },
}]
