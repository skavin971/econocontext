"""Model backends: OpenAI-compatible Chat Completions, and Claude's Messages API.

The wire format is OpenAI's; the provider need not be. Google's Vertex
endpoint is reached through this same class, which is why the header and
scheme are configurable rather than a hardcoded bearer token. Non-streaming,
and httpx makes no implicit retries.
"""

import json
import os
import re
from typing import Protocol

import httpx

from ..contracts import ModelResponse, digest


class ModelBackend(Protocol):
    async def complete(self, request: dict, context: dict) -> ModelResponse: ...


class CompatibleBackend:
    def __init__(self, config, transport=None):
        self.config = config
        self.transport = transport

    async def complete(self, request, context):
        key = os.environ.get(self.config.credential_env)
        if not key:
            raise ValueError(
                f"Missing credential environment variable {self.config.credential_env}"
            )
        async with httpx.AsyncClient(
            timeout=self.config.timeout, transport=self.transport
        ) as client:
            scheme = self.config.auth_scheme
            response = await client.post(
                self.config.base_url.rstrip("/") + "/chat/completions",
                headers={self.config.auth_header: f"{scheme} {key}" if scheme else key},
                json=wire(request),
            )
            response.raise_for_status()
            data = response.json()
        message = data["choices"][0]["message"]
        # Retain provider content and tool protocol. Gemini's thought signature
        # (extra_content) goes back with the turn; reasoning is kept for the record
        # only (see RESPONSE_ONLY).
        message = {
            k: v
            for k, v in message.items()
            if k in ("role", "content", "tool_calls", "reasoning_content", "extra_content")
            and v is not None
        }
        return ModelResponse(message=recover_tool_call(message), usage=data.get("usage"))


# gpt-oss speaks the "harmony" format, which the server is meant to parse into
# tool_calls. Sometimes it does not, and the call arrives as raw text in content:
#   <|channel|>analysis to=functions.read code<|message|>{"path": ...}<|call|>
HARMONY_CALL = re.compile(r"to=functions\.([\w.-]+).*?<\|message\|>(.*?)<\|call\|>", re.S)


def recover_tool_call(message):
    """Turn a tool call the server failed to parse back into one, and mark it."""
    if message.get("tool_calls") or not isinstance(message.get("content"), str):
        return message
    match = HARMONY_CALL.search(message["content"])
    if not match:
        return message
    try:
        json.loads(match.group(2))
    except ValueError:
        return message
    recovered = dict(message, content=None, _recovered="harmony-text")
    recovered["tool_calls"] = [
        {
            "id": "call_recovered_" + digest(match.group(0))[:16],
            "type": "function",
            "function": {"name": match.group(1), "arguments": match.group(2)},
        }
    ]
    return recovered


# Kept on the stored message for the record, never sent back: Vertex's gpt-oss
# chat template fails ("'dict object' has no attribute 'function'") on some
# histories that carry prior reasoning, and no model here needs it returned.
RESPONSE_ONLY = ("reasoning_content",)


def wire(request):
    """The request as the provider sees it.

    Harness-only keys (leading _) and response-only fields are removed, and an
    empty tool_calls list is dropped rather than sent.
    """
    messages = []
    for message in request["messages"]:
        sent = {
            k: v for k, v in message.items() if not k.startswith("_") and k not in RESPONSE_ONLY
        }
        if sent.get("tool_calls") == []:
            del sent["tool_calls"]
        messages.append(sent)
    return dict(request, messages=messages)


class MessagesBackend:
    """Claude through Anthropic's Messages API, served by Vertex.

    Everything above this class speaks OpenAI-shaped messages and reads one usage
    vector, so this translates both ways. Two things matter for measuring cost:

    - The assistant's own content blocks, thinking included, are carried back
      verbatim under ``_blocks``. The API needs thinking returned during tool use,
      and a re-rendered block would not be byte-identical, which breaks the cache.
    - Caching is explicit: one breakpoint closes the static prefix (tools and
      system prompt), and top-level automatic caching follows the growing tail.
      The provider reports reads and writes separately, and both are passed up.

    SDK retries are off. A retry the SDK hides is a billed call telemetry never
    sees, so failures surface as the httpx errors the agent loop already retries.
    """

    def __init__(self, config, client=None):
        self.config = config
        self.client = client

    def _client(self):
        if self.client is None:
            from anthropic import AsyncAnthropicVertex

            self.client = AsyncAnthropicVertex(
                project_id=self.config.project_id,
                region=self.config.region,
                timeout=self.config.timeout,
                max_retries=0,
            )
        return self.client

    async def complete(self, request, context):
        import anthropic

        try:
            response = await self._client().messages.create(**to_messages(request))
        except anthropic.APIStatusError as exc:
            status = exc.status_code
            raise httpx.HTTPStatusError(
                f"{status} from Messages API: {exc.message}",
                request=httpx.Request("POST", "messages"),
                response=httpx.Response(status, request=httpx.Request("POST", "messages")),
            ) from exc
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            raise httpx.TransportError(str(exc)) from exc
        return from_messages(response.model_dump(mode="json"))


EPHEMERAL = {"type": "ephemeral"}


def to_messages(request):
    """OpenAI-shaped chat request -> Messages API keyword arguments."""
    system, turns = [], []

    def add(role, blocks):
        # The API wants alternating roles; consecutive same-role turns merge, and
        # tool results must lead the user turn that answers the tool calls.
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": list(blocks)})

    for message in request["messages"]:
        role = message["role"]
        if role == "system" and not turns:
            system.append({"type": "text", "text": message["content"]})
        elif role == "assistant":
            blocks = message.get("_blocks")
            if blocks is None:
                blocks = []
                if message.get("content"):
                    blocks.append({"type": "text", "text": message["content"]})
                for call in message.get("tool_calls") or []:
                    function = call["function"]
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call["id"],
                            "name": function["name"],
                            "input": json.loads(function.get("arguments") or "{}"),
                        }
                    )
            add("assistant", blocks)
        elif role == "tool":
            add(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": message["tool_call_id"],
                        "content": message.get("content") or "",
                    }
                ],
            )
        else:
            add("user", [{"type": "text", "text": message.get("content") or ""}])
    if system:
        system[-1]["cache_control"] = EPHEMERAL
    tools = [
        {
            "name": tool["function"]["name"],
            "description": tool["function"].get("description", ""),
            "input_schema": tool["function"].get("parameters") or {"type": "object"},
        }
        for tool in request.get("tools") or []
    ]
    out = dict(
        model=request["model"],
        max_tokens=request.get("max_completion_tokens") or request.get("max_tokens"),
        messages=turns,
        cache_control=EPHEMERAL,
    )
    if system:
        out["system"] = system
    if tools:
        out["tools"] = tools
    return out


def from_messages(data):
    """Messages API response -> OpenAI-shaped message plus the shared usage vector."""
    blocks = data.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    calls = [
        {
            "id": b["id"],
            "type": "function",
            "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})},
        }
        for b in blocks
        if b.get("type") == "tool_use"
    ]
    message = {"role": "assistant", "content": text or None, "_blocks": blocks}
    if calls:
        message["tool_calls"] = calls
    usage = data.get("usage") or {}
    read = usage.get("cache_read_input_tokens") or 0
    written = usage.get("cache_creation_input_tokens") or 0
    prompt = (usage.get("input_tokens") or 0) + read + written
    output = usage.get("output_tokens") or 0
    thinking = (usage.get("output_tokens_details") or {}).get("thinking_tokens") or 0
    return ModelResponse(
        message=message,
        usage=dict(
            prompt_tokens=prompt,
            completion_tokens=output,
            total_tokens=prompt + output,
            # Thinking is already inside output_tokens and bills at the output rate.
            completion_tokens_details=dict(reasoning_tokens=thinking),
            prompt_tokens_details=dict(cached_tokens=read, cache_write_tokens=written),
        ),
    )


def build_backend(config):
    if config.api == "messages":
        return MessagesBackend(config)
    return CompatibleBackend(config)
