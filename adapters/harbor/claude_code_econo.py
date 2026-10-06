"""Harbor agent: Claude Code with EconoContext v2 hooks and a compaction driver.

Why it exists: TBLite runs Claude Code inside each task container (Harbor's `claude-code`
agent). This subclass changes only what EconoContext needs and reuses everything else
(install, model and key environment, transcripts under /logs/agent/sessions):

1. Writes $CLAUDE_CONFIG_DIR/settings.json with command hooks (curl) to the host's hook
   service (econocontext/service.py), tagged with this run's name.
2. Replaces Harbor's final `claude --print` with a driver on the host: the Claude Agent SDK
   talks to `claude` in the container through `docker exec -i`. Between messages it asks the
   service for a pending compaction; when there is one it interrupts the turn, sends
   `/compact <instructions>`, then "continue" (spike 1a, check d).

Use: harbor trial start ... -a adapters.harbor.claude_code_econo:ClaudeCodeEcono
     -m claude-sonnet-5 --agent-kwarg econo_run=<run>
The baseline arm uses the same agent with econo_run=none: same driver, no hooks, no flags,
so the two arms differ only by EconoContext.
"""

import asyncio
import dataclasses
import json
import shlex
import stat
import urllib.parse
import urllib.request
from pathlib import Path

from harbor.agents.installed.claude_code import ClaudeCode

SERVICE_IN_CONTAINER = "http://host.docker.internal:8790"
SERVICE_ON_HOST = "http://127.0.0.1:8790"
HOOK_EVENTS = ["SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse",
               "Stop", "PreCompact", "PostCompact", "SubagentStart", "SubagentStop"]
MAX_COMPACTIONS = 3
CONTINUE = "Continue the task from where you left off."
BACKGROUND = {"FORCE_AUTO_BACKGROUND_TASKS", "ENABLE_BACKGROUND_TASKS"}


def hook_settings(run: str, url: str = SERVICE_IN_CONTAINER) -> dict:
    # Command hooks running curl: Claude Code refuses HTTP hooks to private addresses such as
    # host.docker.internal ("HTTP hook blocked ... private/link-local", spike 1b run 1). The event
    # arrives on stdin and the service's JSON reply is printed; if the service is down, curl
    # prints nothing and the hook changes nothing.
    command = (f"curl -s -m 110 -H 'Content-Type: application/json' -H 'X-Econo-Run: {run}' "
               f"--data-binary @- {url}/hook")
    hook = {"type": "command", "command": command, "timeout": 120}
    return {"hooks": {event: [{**({"matcher": "*"} if "ToolUse" in event else {}), "hooks": [hook]}]
                      for event in HOOK_EVENTS}}


class ClaudeCodeEcono(ClaudeCode):
    def __init__(self, logs_dir: Path, econo_run: str, *args, **kwargs):
        super().__init__(logs_dir, *args, **kwargs)
        self.econo_run = econo_run
        self.hooks_on = econo_run not in ("", "none")
        self._environment = None

    @staticmethod
    def name() -> str:
        return "claude-code-econo"

    async def run(self, instruction, environment, context) -> None:
        self._environment = environment
        await super().run(instruction, environment, context)

    async def exec_as_agent(self, environment, command, env=None, cwd=None, timeout_sec=None):
        if "--output-format=stream-json" not in command:
            result = await super().exec_as_agent(environment, command, env=env, cwd=cwd,
                                                 timeout_sec=timeout_sec)
            if command.startswith("mkdir -p $CLAUDE_CONFIG_DIR") and self.hooks_on:  # Harbor's setup
                sessions = Path(self.logs_dir) / "sessions"  # mounted at /logs/agent/sessions
                sessions.mkdir(parents=True, exist_ok=True)
                (sessions / "settings.json").write_text(json.dumps(hook_settings(self.econo_run), indent=2))
            return result
        instruction = shlex.split(command.split("--print -- ", 1)[1].split(" 2>&1", 1)[0])[0]
        return await self._drive(environment, instruction, env or {})

    async def _container(self, environment) -> str:
        result = await environment._run_docker_compose_command(["ps", "-q", "main"])
        return (result.stdout or "").strip().splitlines()[0]

    async def _drive(self, environment, instruction: str, env: dict) -> None:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

        # Plain Claude Code workers in both arms: Harbor forces them into the background, where a
        # worker's report arrives as a notification no hook can shape (spike 1b; user's decision).
        env = {k: v for k, v in env.items() if k not in BACKGROUND}
        container = await self._container(environment)
        workdir = environment.task_env_config.workdir or "/app"
        flags = " ".join(f"-e {shlex.quote(f'{k}={v}')}" for k, v in env.items())
        wrapper = Path(self.logs_dir) / "econo-claude.sh"
        wrapper.write_text("#!/bin/sh\n"
                           f"exec docker exec -i -w {shlex.quote(workdir)} {flags} {container} "
                           "bash -lc 'export PATH=\"$HOME/.local/bin:$PATH\"; exec claude \"$@\"' claude \"$@\"\n")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
        # Same Claude Code as Harbor's `claude --print`: its own system prompt (the SDK would
        # otherwise send an empty one), default tools, and settings loaded from CLAUDE_CONFIG_DIR
        # (where our hooks are), so the arms differ only by EconoContext.
        options = ClaudeAgentOptions(cli_path=str(wrapper), permission_mode="bypassPermissions",
                                     system_prompt={"type": "preset", "preset": "claude_code"})
        log_path = Path(self.logs_dir) / "claude-code.txt"
        total_cost, compactions = 0.0, 0
        with log_path.open("w") as log:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(instruction)
                while True:
                    result, interrupted = await self._until_result(client, log, ResultMessage,
                                                                   allow_interrupt=compactions < MAX_COMPACTIONS)
                    total_cost += getattr(result, "total_cost_usd", 0) or 0
                    flag = self._flag()
                    if not interrupted or not flag:  # the agent finished its task on its own
                        break
                    compactions += 1
                    await client.query(f"/compact {flag['instructions']}")
                    done, _ = await self._until_result(client, log, ResultMessage, allow_interrupt=False)
                    total_cost += getattr(done, "total_cost_usd", 0) or 0
                    await client.query(CONTINUE)
            # Harbor reads the run's cost from a stream-json result line.
            log.write(json.dumps({"type": "result", "total_cost_usd": total_cost,
                                  "econo_compactions": compactions}) + "\n")

    async def _until_result(self, client, log, result_type, allow_interrupt: bool):
        """Read messages until the turn's result; interrupt the turn when a compaction is due.
        Returns (result, whether we interrupted)."""
        interrupted = False
        async for message in client.receive_messages():
            try:
                record = {"kind": type(message).__name__, **dataclasses.asdict(message)}
            except TypeError:
                record = {"kind": type(message).__name__, "repr": repr(message)}
            log.write(json.dumps(record, default=str) + "\n")
            log.flush()
            if isinstance(message, result_type):
                return message, interrupted
            if allow_interrupt and not interrupted and self._flag():
                interrupted = True
                await client.interrupt()
        return None, interrupted

    def _flag(self) -> dict | None:
        if not self.hooks_on:
            return None
        try:
            # Quote the run name: its "+" (econo+jev) would otherwise be read as a space (run v2x maven).
            url = f"{SERVICE_ON_HOST}/flag?run={urllib.parse.quote(self.econo_run, safe='')}"
            with urllib.request.urlopen(url, timeout=5) as r:
                return json.load(r).get("compact")
        except OSError:
            return None
