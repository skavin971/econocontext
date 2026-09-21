"""Non-streaming Chat Completions; httpx has no implicit request retries."""

import os

import httpx

from ..contracts import ModelResponse


class OpenAIBackend:
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
                json=request,
            )
            response.raise_for_status()
            data = response.json()
        message = data["choices"][0]["message"]
        # Retain provider content and tool protocol; exclude optional response-only fields.
        message = {k: v for k, v in message.items() if k in ("role", "content", "tool_calls")}
        return ModelResponse(message=message, usage=data.get("usage"))
