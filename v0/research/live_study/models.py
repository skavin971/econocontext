"""The three models in the study, and what they really cost.

Rates are Vertex AI list prices, global endpoint, per million tokens, read from
https://cloud.google.com/vertex-ai/generative-ai/pricing on 2026-09-25. The
<=200K and >200K columns are identical for all three.

Three caching regimes, deliberately:
  claude-sonnet-5   explicit breakpoints; a write costs 1.25x input, a read 0.1x
  gemini-3.6-flash  implicit; no write premium, a read 0.1x
  gpt-oss-120b      no cache price listed on Vertex, so every input token is full price

In use (2026-09-25): gemini-3.6-flash and gpt-oss-120b. Claude is skipped for now
by choice; gemini-3.5-flash is kept but unused, because Google's shared capacity
refused most realistic requests to it (research/live_study/README.md).
"""

from econocontext.config import Config, Pricing

REVISION = "vertex-global-2026-09-25"
PROJECT = "gen-lang-client-0621220639"
OPENAPI = (
    f"https://aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/global/endpoints/openapi"
)

MODELS = {
    "sonnet-5": dict(
        api="messages",
        model="claude-sonnet-5",
        project_id=PROJECT,
        region="global",
        pricing=Pricing(
            revision=REVISION,
            input_per_million=2.00,
            cached_per_million=0.20,
            cache_write_per_million=2.50,
            output_per_million=10.00,
        ),
    ),
    "gemini-3.5-flash": dict(
        api="chat_completions",
        base_url=OPENAPI,
        model="google/gemini-3.5-flash",
        credential_env="AGENT_PLATFORM_API_KEY",
        auth_header="x-goog-api-key",
        auth_scheme="",
        output_parameter="max_tokens",
        pricing=Pricing(
            revision=REVISION,
            input_per_million=1.50,
            cached_per_million=0.15,
            output_per_million=9.00,
        ),
    ),
    "gemini-3.6-flash": dict(
        api="chat_completions",
        base_url=OPENAPI,
        model="google/gemini-3.6-flash",
        credential_env="AGENT_PLATFORM_API_KEY",
        auth_header="x-goog-api-key",
        auth_scheme="",
        output_parameter="max_tokens",
        # Promotional through 2026-12-31. From 2027-01-01 the sheet lists
        # 1.50 / 0.15 / 7.50, and runs after that date need a new revision.
        pricing=Pricing(
            revision=REVISION + "-promo-to-2026-12-31",
            input_per_million=0.75,
            cached_per_million=0.075,
            output_per_million=3.75,
        ),
    ),
    "gpt-oss-120b": dict(
        api="chat_completions",
        base_url=OPENAPI,
        model="openai/gpt-oss-120b-maas",
        credential_env="AGENT_PLATFORM_API_KEY",
        auth_header="x-goog-api-key",
        auth_scheme="",
        output_parameter="max_tokens",
        pricing=Pricing(
            revision=REVISION,
            input_per_million=0.09,
            cached_per_million=0.09,
            output_per_million=0.36,
        ),
    ),
}

# The same for every model, so the policy is the only variable. gpt-oss-120b's
# 131K window is the smallest, and it bounds everyone.
CONTEXT_TOKENS = 120_000


def config(name, data_dir, call_log):
    return Config(
        data_dir=data_dir,
        call_log=call_log,
        backend="live",
        live_context_tokens=CONTEXT_TOKENS,
        timeout=300,
        **MODELS[name],
    )
