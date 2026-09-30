"""How an agent runs on Omnigent: its spec, one session, and what Omnigent recorded.

Why it exists: every benchmark (benchmarks/<name>/run.py) and every harness check
(smoke.py, probe.py) needs the same three things, whatever the task: write the agent's
spec for a run, run one Omnigent session with one prompt, and read the session back.
This is the only file that imports Omnigent's own code (tests/unit/test_isolation.py).

Harnesses (specs under harness/specs/):
  openai-controlled   Omnigent's openai-agents harness: our spec, tools and workers;
                      the EconoContext policy in the econo arm
  gemini-omnigent     stock Gemini CLI over ACP (specs/gemini/); measured at the gateway
What it must never do: know anything about a benchmark's tasks or grading.
"""

import asyncio
import re
import shutil
from pathlib import Path
from string import Template

from omnigent.chat import (_prepare_chat_session_via_daemon, _remote_headers, _server_auth,
                           _stop_headless_session)
from omnigent.cli import _bundle
from omnigent.host.identity import load_or_create_host_identity
from omnigent_client import OmnigentClient, SessionsChat, StreamHooks

from omnigent_layer import HOME

SPECS = Path(__file__).parent / "specs"
GEMINI = HOME / "data" / "tools" / "node_modules" / ".bin" / "gemini"  # pinned: docs/harness-baseline.md
GEMINI_MODEL = "gemini-3.6-flash"
POLICY = """
policies:
  econocontext:
    type: function
    handler: omnigent_layer.policy.econocontext
    factory_params: {run_id: "$run_id", workdir: "$workdir"}
"""


def write_spec(harness: str, run_id: str, workdir: Path, gateway: str, econo: bool) -> Path:
    """The run's agent spec, filled in. openai-controlled: specs/openai_agents.yaml (+ the policy
    in the econo arm). gemini-omnigent: specs/gemini/agent.yaml, never with a policy."""
    values = {"run_id": run_id, "gateway": gateway, "workdir": str(workdir)}
    safe = re.sub(r"[^\w.-]", "_", run_id)
    if harness == "gemini-omnigent":
        # Gemini's own HOME per run: its settings (Vertex auth; in ACP mode Gemini reads
        # the auth type only from settings) and, afterwards, its session files.
        home = HOME / "data" / "work" / f"{safe}.home"
        (home / ".gemini").mkdir(parents=True, exist_ok=True)
        shutil.copy(SPECS / "gemini" / "settings.json", home / ".gemini" / "settings.json")
        text = (SPECS / "gemini" / "agent.yaml").read_text()
        values.update(gemini=str(GEMINI), model=GEMINI_MODEL, home=str(home),
                      path=f"{Path(shutil.which('node') or '/usr/bin/node').parent}:/usr/bin:/bin")
    else:
        text = (SPECS / "openai_agents.yaml").read_text() + (POLICY if econo else "")
    spec = HOME / "data" / "work" / f"{safe}.agent.yaml"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(Template(text).substitute(values))
    return spec


def approve_all(ctx) -> bool:
    """Headless: accept an approval card (and say what asked), instead of the client's
    default decline, which ends the turn. Gemini asks for some shell commands even with
    --approval-mode yolo."""
    print(f"approved: {ctx.policy_name or ctx.phase or 'harness'}: {ctx.message[:160]}", flush=True)
    return True


async def run_session(server: str, spec: Path, workdir: Path, prompt: str,
                      seconds: float, approve: bool = False) -> tuple[str, str]:
    """One Omnigent session, one task. Returns the reply and the session id. `approve`
    accepts approval cards (Gemini runs); otherwise the client declines them."""
    # The same path `omnigent run` takes: the host daemon launches a runner for the new
    # session. These helpers are private to Omnigent 0.15.0 (pinned); re-check on upgrade.
    prepared = await _prepare_chat_session_via_daemon(
        base_url=server, headers=_remote_headers(server_url=server, host_id=None),
        auth=_server_auth(server_url=server, session_id=None),
        host_id=load_or_create_host_identity().host_id, bundle=_bundle(spec),
        resume_conversation_id=None, fork_session_id=None, workspace=str(workdir))
    async with OmnigentClient(base_url=server) as client:
        bound = await client.sessions.get(prepared.session_id)
        print(f"session {server}/c/{bound.id}", flush=True)
        files = client.files.for_session(bound.id)
        chat = SessionsChat(namespace=client.sessions, files_uploader=files.upload,
                            files_getter=files.get, session=bound,
                            hooks=StreamHooks(on_elicitation_request=approve_all) if approve else None)
        try:
            result = await asyncio.wait_for(chat.query(prompt), timeout=seconds)
        finally:
            _stop_headless_session(base_url=server, session_id=bound.id)
        return getattr(result, "text", "") or "", bound.id


async def session_view(server: str, session_id: str) -> dict:
    """What Omnigent recorded for a session: its item types, the tool calls as the harness
    reported them, and the child (sub-agent) sessions it created."""
    async with OmnigentClient(base_url=server) as client:
        items, after = [], None
        while True:
            page = await client.sessions.list_items(session_id, limit=1000, after=after)
            items += page
            if len(page) < 1000:
                break
            after = page[-1].get("id")
        children = await client.sessions.child_sessions(session_id)
    types: dict[str, int] = {}
    for i in items:
        types[str(i.get("type"))] = types.get(str(i.get("type")), 0) + 1
    return {"item_types": types,
            "tool_calls": [{"name": i.get("name"), "arguments": str(i.get("arguments") or "")[:300]}
                           for i in items if i.get("type") in ("function_call", "tool_call")],
            "child_sessions": [{k: c.get(k) for k in ("id", "title", "tool", "agent_name", "busy",
                                                       "current_task_status")} for c in children]}
