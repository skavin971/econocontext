"""How an agent runs on Omnigent: its spec, one session, and what Omnigent recorded.

Why it exists: every benchmark (benchmarks/<name>/run.py) and every harness check
(smoke.py, probe.py) needs the same three things, whatever the task: write the agent's
spec for a run, run one Omnigent session with one prompt, and read the session back.
This is the only file that imports Omnigent's own code (tests/unit/test_isolation.py).

Harnesses (specs under harness/specs/):
  openai-controlled   Omnigent's openai-agents harness: our spec, tools and workers;
                      the EconoContext policy in the econo arm
  gemini-omnigent     stock Gemini CLI over ACP (specs/gemini/); measured at the gateway
  claude-code         stock Claude Code, Omnigent's claude-native (specs/claude_code/); its
                      model calls go to the gateway's /anthropic route
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
CLAUDE_MODEL = "claude-sonnet-5"  # the only Claude model we use; every setting is pinned to it
POLICY = """
policies:
  econocontext:
    type: function
    handler: omnigent_layer.policy.econocontext
    factory_params: {run_id: "$run_id", workdir: "$workdir"}
"""
# The benchmark container's shell, for a harness that takes our function tools
# (openai-agents already declares it in its spec). Added only when asked for.
TESTBED_SHELL = """
tools:
  testbed_shell:
    type: function
    callable: omnigent_layer.tools.container_shell
    description: Run a bash command in the repository's own environment (cwd is the repository root). Returns exit code, stdout and stderr.
    parameters:
      type: object
      properties:
        command: {type: string, description: The bash command to run.}
      required: [command]
"""


def write_spec(harness: str, run_id: str, workdir: Path, gateway: str, econo: bool,
               container: bool = False) -> Path:
    """The run's agent spec, filled in. openai-controlled: specs/openai_agents.yaml (+ the policy
    in the econo arm). gemini-omnigent: specs/gemini/agent.yaml, never with a policy.
    claude-code: specs/claude_code/ (+ the policy in the econo arm). `container` adds
    testbed_shell for harnesses that take our function tools."""
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
    elif harness == "claude-code":
        # Claude Code reads its gateway URL, placeholder key and model pins from the
        # workspace's local settings (git-excluded by whoever made the workspace).
        settings = Template((SPECS / "claude_code" / "settings.json").read_text()).substitute(values)
        (workdir / ".claude").mkdir(parents=True, exist_ok=True)
        (workdir / ".claude" / "settings.local.json").write_text(settings)
        text = (SPECS / "claude_code" / "agent.yaml").read_text() + (POLICY if econo else "") \
            + (TESTBED_SHELL if container else "")
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


BUSY = ("running", "waiting")


def check_measured(engine, status: str) -> str:
    """A run whose model calls never reached the gateway was not measured (the harness
    used some other credential, e.g. a local login). Say so instead of "done"."""
    calls = engine.db.rows("SELECT COUNT(*) n FROM outcomes WHERE run_id=?", (engine.run_id,))[0]["n"]
    return status if calls or status != "done" else "not measured: no model call reached the gateway"


def latest_call(engine):
    """For run_session(activity=...): the time of the run's latest model call."""
    return lambda: engine.db.rows("SELECT MAX(started_at) t FROM runtime_spans WHERE run_id=? "
                                  "AND kind='model'", (engine.run_id,))[0]["t"]


def check_settings(harness: str, workdir: Path) -> None:
    """Claude Code must find its per-run settings, or it would fall back to a local login
    and bypass the gateway. Raise before the session starts if they are missing."""
    if harness == "claude-code" and not (workdir / ".claude" / "settings.local.json").exists():
        raise RuntimeError(f"{workdir}/.claude/settings.local.json is missing: Claude Code "
                           f"would bypass the gateway")


async def wait_until_done(client, session_id: str, seconds: float,
                          activity=None, quiet: float = 30.0) -> None:
    """For a native harness (Claude Code): Omnigent completes the turn as soon as the
    prompt is typed into the terminal, and the work shows up afterwards. Claude Code can
    also end its own turn while a background sub-agent works, and resume when it reports.
    So: done once the session has been busy, and then nothing has happened for `quiet`
    seconds: the session and every sub-agent idle, and (if `activity` is given, a function
    returning the time of the run's latest model call) no new model call."""
    loop = asyncio.get_running_loop()
    deadline, seen_busy = loop.time() + seconds, False
    last_change, last_activity = loop.time(), activity() if activity else None
    while loop.time() < deadline:
        session = await client.sessions.get(session_id)
        busy = session.status in BUSY or await client.sessions.subtree_busy(session_id)
        now_activity = activity() if activity else None
        if busy or now_activity != last_activity:
            seen_busy, last_change, last_activity = True, loop.time(), now_activity
        elif loop.time() - last_change >= (quiet if seen_busy else 3 * quiet):
            return
        await asyncio.sleep(3)
    raise TimeoutError


async def last_reply(client, session_id: str) -> str:
    """The text of the session's last assistant message."""
    items = await client.sessions.list_items(session_id, limit=100, order="desc")
    for item in items:
        if item.get("type") == "message" and item.get("role") == "assistant":
            content = item.get("content")
            if isinstance(content, str):
                return content
            return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict))
    return ""


async def run_session(server: str, spec: Path, workdir: Path, prompt: str,
                      seconds: float, approve: bool = False,
                      native: bool = False, activity=None) -> tuple[str, str]:
    """One Omnigent session, one task. Returns the reply and the session id. `approve`
    accepts approval cards (Gemini and Claude Code runs); otherwise the client declines
    them. `native` waits for a terminal-driven harness (Claude Code) to finish; `activity`
    (the time of the run's latest model call) keeps that wait going while calls still come."""
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
            if native:
                await wait_until_done(client, bound.id, seconds, activity)
                return await last_reply(client, bound.id), bound.id
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
