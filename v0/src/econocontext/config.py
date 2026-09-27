import os
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Record, digest


class Pricing(Record):
    revision: str = "explicit-v1"
    input_per_million: float = Field(ge=0)
    cached_per_million: float = Field(ge=0)
    output_per_million: float = Field(ge=0)
    # Explicit-cache providers bill writing a prefix into the cache at a premium
    # (Anthropic: 1.25x input for the 5-minute TTL). Implicit caches have no write
    # premium, so None bills written tokens at the ordinary input rate.
    cache_write_per_million: float | None = Field(None, ge=0)


def load_env_file(path=Path(".env")):
    """Populate os.environ from a KEY=VALUE file; existing variables always win."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


class Config(Record):
    data_dir: Path = Path("data")
    backend: Literal["fake", "live"] = "fake"
    base_url: str = "https://api.openai.com/v1"
    model: str = "fake-model"
    credential_env: str = "OPENAI_API_KEY"
    # Google's OpenAI-compatible endpoints reject Bearer and require x-goog-api-key
    # with a bare value, so both the header and its scheme are configurable.
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    # Which wire protocol the model speaks. Messages is Claude on Vertex, which
    # authenticates with Google credentials (ADC), not an API key.
    api: Literal["chat_completions", "messages"] = "chat_completions"
    project_id: str | None = None
    region: str = "global"
    output_parameter: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"
    timeout: float = Field(30, gt=0)
    pricing: Pricing | None = None
    live_context_tokens: int | None = Field(None, ge=256)
    profile_path: Path | None = None
    # Directory for per-run call logs. Each run writes its own
    # run-<unix>-<run_id>.txt holding every request and response in full.
    call_log: Path | None = None
    # Whether the worker is offered request_operation. Planning is the harness's
    # job, so a live model is never told workers exist; the stand-in used in
    # tests keeps the delegation-request path exercised. None resolves by backend.
    delegation_tool: bool | None = None
    # How a long-lived worker's history is managed; see context.py. The threshold
    # and floor are shared by every policy that uses them, so a comparison
    # between policies varies only the rule.
    context_policy: Literal["append-only", "compact", "clear-oldest", "cache-aware"] = "append-only"
    context_trigger: int = Field(60_000, ge=1)
    context_keep: int = Field(3, ge=0)
    # Seconds after which the provider's prompt cache is assumed cold.
    cache_ttl: float = Field(300, gt=0)
    # Forecast of a root's total calls. When set, every placement is also charged
    # for holding what it leaves in the root: one cache write, then a cached read
    # on each call the root is forecast to still make. None prices execution only.
    lifetime_prior: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def live_capacity(self):
        if self.backend == "live" and (not self.live_context_tokens or self.model == "fake-model"):
            raise ValueError("Live backend requires model and explicit live_context_tokens")
        if self.delegation_tool is None:
            object.__setattr__(self, "delegation_tool", self.backend == "fake")
        return self

    def fingerprint(self) -> str:
        # Observability paths and credentials never change model behaviour, so they
        # stay out of the fingerprint that gates worker reuse and profile matching.
        return digest(
            self.model_dump(
                mode="json",
                exclude={"data_dir", "credential_env", "profile_path", "call_log"},
            )
        )

    @classmethod
    def from_env(cls):
        load_env_file()
        prefix = "ECONOCONTEXT_"
        values = {
            k: os.environ[prefix + k.upper()]
            for k in cls.model_fields
            if prefix + k.upper() in os.environ
        }
        if "pricing" in values:
            import json

            values["pricing"] = json.loads(values["pricing"])
        return cls(**values)
