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


class Config(Record):
    data_dir: Path = Path("data")
    backend: Literal["scripted", "openai"] = "scripted"
    base_url: str = "https://api.openai.com/v1"
    model: str = "scripted-v1"
    credential_env: str = "OPENAI_API_KEY"
    output_parameter: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"
    timeout: float = Field(30, gt=0)
    pricing: Pricing | None = None
    live_context_tokens: int | None = Field(None, ge=256)
    profile_path: Path | None = None

    @model_validator(mode="after")
    def live_capacity(self):
        if self.backend == "openai" and (
            not self.live_context_tokens or self.model == "scripted-v1"
        ):
            raise ValueError("Live backend requires model and explicit live_context_tokens")
        return self

    def fingerprint(self) -> str:
        return digest(
            self.model_dump(mode="json", exclude={"data_dir", "credential_env", "profile_path"})
        )

    @classmethod
    def from_env(cls):
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
