"""Harbor agent: a small tool-calling agent for Gemini that owns its conversation.

Why it exists: the full-control track of EconoContext v2. The agent runs on the host, keeps
the conversation as a Python list, and runs its tools inside the task's container. In the raw
arm the list is sent as it is; in the econo arms EconoContext (econocontext/owner.py) sees every
tool call and result and may edit the list before each model call. Same model, tools, prompts
and limits in every arm, so arms differ only by EconoContext.

Model calls go to our gateway's OpenAI-compatible route (non-streaming, so it passes the bytes
through unchanged and records each call's usage and cost). Vertex quirks (EconoCLM): echo each
assistant message whole (it carries `extra_content.google.thought_signature`), never end the list
on an assistant turn, and resend a call that ends with `malformed_function_call`.

Use: harbor trial start -p <task> -e docker -a adapters.harbor.econo_agent:EconoAgent
     -m openai/google/gemini-3.6-flash --agent-kwarg api_base=http://127.0.0.1:8787/run/<run>/v1
     [--agent-kwarg econo=jev|prior --agent-kwarg econo_run=<run>]
"""

import asyncio
import base64
import json
import shlex
import time
from pathlib import Path

from harbor.agents.base import BaseAgent

SYSTEM = (
    "You are an autonomous agent completing a task in a Linux container. Work in the current "
    "directory unless the task says otherwise. Use the tools to look around, run commands, and read "
    "and write files. Check your work. When the task is fully done, call `submit`.")
NUDGE = "Continue by calling a tool. Call `submit` when the task is fully done."
STATE = "/tmp/.econo-agent"
TOOLS = [
    {"type": "function", "function": {
        "name": "bash", "description": "Run a bash command in the container; returns its output and exit code.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read lines of a text file, with line numbers (default: lines 1-400).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file", "description": "Write a whole text file (creates directories; replaces the file).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                       "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "submit", "description": "Finish: call this once the task is fully done.",
        "parameters": {"type": "object", "properties": {}}}},
]
RETRYABLE = {408, 409, 429, 500, 502, 503, 504, 529}


def cap(text: str, limit: int) -> str:
    """Head and tail of a long output (the same cap in every arm)."""
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n... [{len(text) - limit} characters omitted] ...\n{text[-half:]}"


class EconoAgent(BaseAgent):
    def __init__(self, logs_dir, model_name=None, api_base=None, econo="off", econo_run=None,
                 max_steps=60, max_tokens=8192, temperature=0.7, command_timeout=180,
                 output_chars=30000, client=None, state_dir=STATE, force=None, **kwargs):
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        self.model = (model_name or "").removeprefix("openai/")
        self.econo, self.econo_run = econo, econo_run
        self.force = set(force or [])  # spike only: rules applied whenever they apply
        self.max_steps, self.max_tokens, self.temperature = int(max_steps), int(max_tokens), float(temperature)
        self.command_timeout, self.output_chars = int(command_timeout), int(output_chars)
        if client is None:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(base_url=api_base, api_key="placeholder", timeout=600, max_retries=0)
        self.client = client
        self.state = state_dir
        self.messages: list[dict] = []
        self.calls: list[dict] = []   # one row per model call: usage, finish reason, latency
        self.owner = None             # econocontext.owner.Owner in the econo arms

    @staticmethod
    def name() -> str:
        return "econo-agent"

    def version(self) -> str:
        return "0.1"

    async def setup(self, environment) -> None:
        await environment.exec(f"mkdir -p {self.state} && pwd > {self.state}/cwd", timeout_sec=30)

    # Tools ----------------------------------------------------------------------------
    async def _shell(self, environment, script: str) -> str:
        """Run in the saved working directory; save it again afterwards (cd persists)."""
        wrapped = (f"cd \"$(cat {self.state}/cwd 2>/dev/null || pwd)\" 2>/dev/null\n{script}\n"
                   f"__rc=$?; pwd > {self.state}/cwd; exit $__rc")
        command = f"timeout -k 5 {self.command_timeout} bash -c {shlex.quote(wrapped)}"
        try:
            result = await environment.exec(command, timeout_sec=self.command_timeout + 30)
        except RuntimeError as exc:  # Harbor raises on its own timeout
            return f"(command failed: {str(exc)[:200]})"
        out = (result.stdout or "") + (("\n" + result.stderr) if result.stderr else "")
        code = result.return_code
        return out.rstrip("\n") + f"\n(exit code {code}{', timed out' if code == 124 else ''})"

    async def tool(self, environment, name: str, args: dict) -> str:
        """Run one tool; the full output (the econo arms store it; what is shown is decided later)."""
        if name == "bash":
            return await self._shell(environment, args.get("command", ""))
        if name == "read_file":
            start = max(1, int(args.get("start_line") or 1))
            end = int(args.get("end_line") or start + 399)
            path = shlex.quote(args.get("path", ""))
            return await self._shell(environment, (
                f"total=$(wc -l < {path} | tr -d ' ') || exit 1; last=$(( {end} < total ? {end} : total )); "
                f"awk -v s={start} -v e={end} 'NR>=s && NR<=e {{printf \"%d\\t%s\\n\", NR, $0}}' {path}; "
                f"echo \"[lines {start}-$last of $total]\""))
        if name == "write_file":
            path = args.get("path", "")
            data = base64.b64encode(args.get("content", "").encode()).decode()
            return await self._shell(environment, (
                f"mkdir -p \"$(dirname {shlex.quote(path)})\" && printf %s {data} | base64 -d > {shlex.quote(path)} "
                f"&& echo \"wrote $(wc -c < {shlex.quote(path)} | tr -d ' ') bytes to {path}\""))
        return f"(unknown tool {name})"

    # Model ----------------------------------------------------------------------------
    async def complete(self, messages: list[dict]):
        """One model call with retries: transient errors back off; a malformed tool call is resent."""
        malformed, delay = 0, 2.0
        for attempt in range(8):
            started = time.monotonic()
            try:
                reply = await self.client.chat.completions.create(
                    model=self.model, messages=messages, tools=TOOLS, max_tokens=self.max_tokens,
                    temperature=self.temperature)
            except Exception as exc:  # openai's error types carry status_code; others are transport errors
                status = getattr(exc, "status_code", None)
                if status is not None and status not in RETRYABLE:
                    raise
                if any(word in str(exc).lower() for word in ("reached", "budget", "spend cap")):
                    raise  # the gateway refused (call cap, daily tokens, money): stop, don't retry
                await asyncio.sleep(delay)
                delay = min(60.0, delay * 2)
                continue
            choice = reply.choices[0] if reply.choices else None
            self.calls.append({"usage": reply.usage.model_dump() if reply.usage else None,
                               "finish_reason": choice.finish_reason if choice else "no_choice",
                               "latency_ms": round((time.monotonic() - started) * 1000)})
            if choice is not None and choice.finish_reason == "malformed_function_call" and malformed < 3:
                malformed += 1
                continue
            return reply
        raise RuntimeError("model call failed after retries")

    # The loop -------------------------------------------------------------------------
    async def run(self, instruction, environment, context) -> None:
        self.messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": instruction}]
        if self.econo != "off":
            from econocontext.owner import Owner
            self.owner = Owner(self.econo_run, self.econo, Path(self.logs_dir) / "session.sqlite3",
                               force=self.force)
            self.owner.start(instruction)
        try:
            for _ in range(self.max_steps):
                sent = await self.owner.before_call(self.messages, self.calls, self.complete) \
                    if self.owner else self.messages
                reply = await self.complete(sent)
                self._account(context)
                choice = reply.choices[0] if reply.choices else None
                if choice is None or choice.message is None:
                    # Gemini can return a reply without a message (content filter, empty reply; EconoCLM
                    # saw both). Treat it as a turn with no tool call: nudge and go on (gx1 raw crashed here).
                    self.messages.append({"role": "user", "content": NUDGE})
                    continue
                message = choice.message.model_dump(exclude_none=True)
                self.messages.append(message)
                calls = message.get("tool_calls") or []
                if not calls:
                    self.messages.append({"role": "user", "content": NUDGE})
                    continue
                if await self._run_calls(environment, calls):
                    break
        finally:
            self._save()

    async def _run_calls(self, environment, calls: list[dict]) -> bool:
        """Answer every tool call of one assistant turn (Vertex needs a result for each)."""
        done = False
        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except ValueError:
                args, name = {}, f"{name} (invalid arguments)"
            if name == "submit":
                done, shown = True, "Submitted."
            else:
                served = self.owner.before_tool(name, args) if self.owner else None
                full = served if served is not None else await self.tool(environment, name, args)
                shown = self.owner.after_tool(name, args, full, served is not None, call["id"]) \
                    if self.owner else full
                shown = cap(shown, self.output_chars)
            self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": shown})
        return done

    def _account(self, context) -> None:
        """Keep Harbor's context current after every call (it may cancel run() on timeout)."""
        usages = [c["usage"] for c in self.calls if c["usage"]]
        context.n_input_tokens = sum(u.get("prompt_tokens") or 0 for u in usages)
        context.n_cache_tokens = sum((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0 for u in usages)
        context.n_output_tokens = sum((u.get("completion_tokens") or 0)
                                      + ((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
                                      for u in usages)

    def _save(self) -> None:
        Path(self.logs_dir).mkdir(parents=True, exist_ok=True)
        (Path(self.logs_dir) / "trajectory.json").write_text(json.dumps(
            {"model": self.model, "econo": self.econo, "messages": self.messages, "calls": self.calls,
             "owner": self.owner.report() if self.owner else None}, indent=1, default=str))
