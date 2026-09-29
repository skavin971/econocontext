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

What it must never do: deny or delay a tool, or fail one. Every error abstains, and
Omnigent then does exactly what it would have done.
"""

import hashlib
import json
import logging
import os

from econocontext.types import ToolCallEvent, ToolResultEvent

from . import agent_id, engine_for
from .workspace import Workspace

log = logging.getLogger("econocontext.policy")

# Omnigent 0.15.0 OS tools, plus the bench's container shell. Reads name their path.
READS = {"sys_os_read": "path"}
WRITES = {"sys_os_write", "sys_os_edit", "sys_os_shell", "testbed_shell"}


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
