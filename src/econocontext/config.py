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
