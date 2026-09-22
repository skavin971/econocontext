from typing import Protocol

from ..contracts import ModelResponse


class ModelBackend(Protocol):
    async def complete(self, request: dict, context: dict) -> ModelResponse: ...
