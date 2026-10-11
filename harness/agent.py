"""Harbor agent: our own harness, one tool-calling loop for every policy (step 11).

Why it exists: every arm of ours runs this same loop, with the same model settings, interface, tools,
budget gate and logging; a policy is the only part that differs. The agent runs on the host, keeps
the conversation as a Python list, and runs its tools inside the task's container.

A policy is given by name (`policy`, "module:Class", with `policy_args`); the runner maps --policy to
it, and the harness imports it by that name, naming no policy package itself. Every policy has the
same hooks:
- start(instruction, harness);
- before_call(messages, harness): may edit the conversation in place; returns the messages to send;
- before_tool(name, args): an output to serve instead of running the tool, or None;
- after_tool(name, args, output, served, call_id): what the model is shown;
- report(): what the policy did, for the trajectory.
It also says whether a budget is "allowed", "required" or "refused".

Model calls go only to the api_base the agent is given: our gateway (gateway/; there is no default
upstream). Calls are non-streaming; each carries X-Econo-Call-Kind: agent, or summary for a policy's
compaction call, so the gateway's log can tell them apart. Every call asks for CLM's TBLite sampling
and output cap (step 10b): temperature 0.7, top_p 0.95, thinking on, reasoning_effort medium,
max_tokens 2,048 (the values the gateway pins). The step limit is 64.

With a budget, CLM's budget gate (budget.py) runs before every call. A run ends with `end` =
submitted, max_steps, budget_exceeded or overflow (the server refused a request over the model's
window); every ending returns normally, so the verifier grades the container as it stands.

Use: harbor trial start -p <task> -e docker -a harness.agent:Agent -m openai/qwen3.8:27b
     --agent-kwarg api_base=http://127.0.0.1:8787/run/<run>/v1 --agent-kwarg run_id=<run>
     --agent-kwarg policy=<module:Class> [--agent-kwarg 'policy_args={...}'] [--agent-kwarg budget=32000]
     [--agent-kwarg interface=ours|legacy]
"""

import asyncio
import importlib
import json
import time
from pathlib import Path

from harbor.agents.base import BaseAgent

from .budget import BudgetGate
from .interface import INTERFACES, TOOLS
from .tools import STATE, cap, run_tool, setup

RETRYABLE = {408, 409, 429, 500, 502, 503, 504, 529}
TEMPLATE = {"reasoning_effort": "medium", "chat_template_kwargs": {"enable_thinking": True}}   # as the gateway pins them


def overflowed(exc: Exception) -> bool:
    """The server refused the request for exceeding the model's window (vLLM's error through LiteLLM)."""
    return getattr(exc, "status_code", None) == 400 and "ContextWindowExceeded" in str(exc)


def load(path: str):
    """A class given by name, "module:Class"."""
    module, _, name = path.partition(":")
    return getattr(importlib.import_module(module), name)


class Agent(BaseAgent):
    def __init__(self, logs_dir, model_name=None, api_base=None, policy=None, policy_args=None, interface="ours",
                 run_id=None, max_steps=64, max_tokens=2048, temperature=0.7, top_p=0.95, command_timeout=180,
                 output_chars=30000, client=None, state_dir=STATE, budget=None, counter=None, **kwargs):
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        if policy is None:
            raise ValueError("a policy is required (--policy)")
        args = policy_args if isinstance(policy_args, dict) else json.loads(policy_args) if policy_args else {}
        self.policy = load(policy)(**args) if isinstance(policy, str) else policy
        self.interface = INTERFACES[interface] if isinstance(interface, str) else interface
        self.model = (model_name or "").removeprefix("openai/")
        self.run_id = run_id
        self.max_steps, self.max_tokens, self.temperature = int(max_steps), int(max_tokens), float(temperature)
        self.top_p = float(top_p)
        self.command_timeout, self.output_chars = int(command_timeout), int(output_chars)
        self.budget = int(budget) if str(budget).lower() not in ("none", "", "0") else None
        rule = getattr(self.policy, "budget", "allowed")
        if self.budget and rule == "refused":
            raise ValueError(getattr(self.policy, "budget_refusal", f"the {self.policy.name} policy refuses a budget"))
        if not self.budget and rule == "required":
            raise ValueError(f"the {self.policy.name} policy needs a budget")
        self.gate = BudgetGate(self.budget) if self.budget else None
        self.counter = counter        # (messages, tools) -> tokens; measure.tokens.count_prompt unless a test gives one
        if client is None:
            if not api_base:
                raise ValueError("api_base is required: agents reach models only through gateway/")
            from openai import AsyncOpenAI
            client = AsyncOpenAI(base_url=api_base, api_key="placeholder", timeout=600, max_retries=0)
        self.client = client
        self.state = state_dir
        self.messages: list[dict] = []
        self.calls: list[dict] = []   # one row per model call: usage, finish reason, latency
        self.end, self.end_P = None, None

    @staticmethod
    def name() -> str:
        return "econo-harness"

    def version(self) -> str:
        return "0.2"

    async def setup(self, environment) -> None:
        await setup(environment, self.state)

    # Model ----------------------------------------------------------------------------
    async def complete(self, messages: list[dict], kind: str = "agent", tools: list[dict] | None = TOOLS):
        """One model call with retries: transient errors back off; a malformed tool call is resent.
        `kind` goes to the gateway's log: agent, or summary for a compaction call. `tools` None sends
        no tools (Codex's compaction call)."""
        malformed, delay = 0, 2.0
        for attempt in range(8):
            started = time.monotonic()
            try:
                reply = await self.client.chat.completions.create(
                    model=self.model, messages=messages, **({"tools": tools} if tools else {}), max_tokens=self.max_tokens,
                    temperature=self.temperature, top_p=self.top_p, reasoning_effort=TEMPLATE["reasoning_effort"],
                    extra_body={"chat_template_kwargs": TEMPLATE["chat_template_kwargs"]},
                    extra_headers={"X-Econo-Call-Kind": kind})
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

    def count(self, messages: list[dict], tools: list[dict] | None = TOOLS) -> int:
        """The prompt's tokens as the server will count them (measure/tokens.py), with the request's tools."""
        if self.counter is None:
            from measure.tokens import count_prompt
            self.counter = lambda m, t: count_prompt(m, t, self.model, **TEMPLATE)
        return self.counter(messages, tools)

    def cut(self, reply) -> bool:
        """The reply stopped at the output limit (vLLM reports a cut tool call as tool_calls, step 10b)."""
        choice = reply.choices[0] if reply.choices else None
        used = (reply.usage.completion_tokens or 0) if reply.usage else 0
        return (choice is not None and choice.finish_reason == "length") or used >= self.max_tokens

    # The loop -------------------------------------------------------------------------
    async def run(self, instruction, environment, context) -> None:
        self.messages = [{"role": "system", "content": self.interface.system}, {"role": "user", "content": instruction}]
        self.policy.start(instruction, self)
        try:
            for _ in range(self.max_steps):
                sent = await self.policy.before_call(self.messages, self)
                if self.gate and not self.gate.allows(sent, self.count(sent), len(self.calls) + 1):
                    self.end, self.end_P = "budget_exceeded", self.gate.end_P
                    break
                try:
                    reply = await self.complete(sent)
                except Exception as exc:
                    if not overflowed(exc):
                        raise
                    self.end = "overflow"
                    break
                self._account(context)
                choice = reply.choices[0] if reply.choices else None
                if choice is None or choice.message is None:
                    # A reply without a message (Gemini: a content filter, an empty reply): a turn with no call.
                    self.messages.append({"role": "user", "content": self.interface.nudge})
                    continue
                message = choice.message.model_dump(exclude_none=True)
                self.messages.append(message)
                cut = self.cut(reply)
                calls = message.get("tool_calls") or []
                if not calls:
                    self.messages.append({"role": "user",
                                          "content": self.interface.nudge_cut if cut else self.interface.nudge})
                    continue
                if await self._run_calls(environment, calls, cut):
                    self.end = "submitted"
                    break
            else:
                self.end = "max_steps"
        finally:
            self._save()

    async def _run_calls(self, environment, calls: list[dict], cut: bool) -> bool:
        """Answer every tool call of one reply (each needs a result). The interface decides which run."""
        done = False
        for i, call in enumerate(calls):
            name = call["function"]["name"]
            if cut and not self.interface.run_cut_calls:
                shown = self.interface.cut_call
            elif i > 0 and self.interface.one_call:
                shown = self.interface.reject_extra
            else:
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except ValueError:
                    args, name = {}, f"{name} (invalid arguments)"
                if name == "submit":
                    done, shown = True, "Submitted."
                else:
                    served = self.policy.before_tool(name, args)
                    full = served if served is not None else \
                        await run_tool(environment, self.state, self.command_timeout, name, args)
                    shown = cap(self.policy.after_tool(name, args, full, served is not None, call["id"]),
                                self.output_chars)
            self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": shown})
        return done

    def _account(self, context) -> None:
        """Keep Harbor's context current after every call (it may cancel run() on timeout)."""
        usages = [c["usage"] for c in self.calls if c["usage"]]
        context.n_input_tokens = sum(u.get("prompt_tokens") or 0 for u in usages)
        context.n_cache_tokens = sum((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0 for u in usages)
        # completion_tokens already includes the thinking on vLLM (reasoning_tokens is a detail of it).
        context.n_output_tokens = sum(u.get("completion_tokens") or 0 for u in usages)

    def _save(self) -> None:
        Path(self.logs_dir).mkdir(parents=True, exist_ok=True)
        (Path(self.logs_dir) / "trajectory.json").write_text(json.dumps(
            {"model": self.model, "policy": self.policy.name, "interface": self.interface.name, "budget": self.budget,
             "end": self.end, "end_P": self.end_P, "final_turns": self.gate.final_turns if self.gate else [],
             "report": self.policy.report(), "messages": self.messages, "calls": self.calls}, indent=1, default=str))
