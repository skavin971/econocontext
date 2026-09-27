"""A Deep Agents coding agent. It knows nothing about EconoContext.

Why it exists: EconoContext has to be tested inside a real, unmodified harness.
This builds a stock Deep Agents agent (deepagents 0.7.19): Gemini on Vertex AI,
the sandbox backend on the instance image, and Deep Agents' general-purpose
subagent for delegation. Extra middleware and callbacks are generic parameters;
the composition root decides what goes in them.
What it must never do: import EconoContext or the adapter.

The general-purpose subagent is passed explicitly, mirroring Deep Agents' own
default (same name, prompt and tools), because the default one does not accept
new middleware. Both arms get the same explicit spec, so they differ only in the
middleware passed in.
"""

import os
import warnings

from deepagents import create_deep_agent
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_google_genai import ChatGoogleGenerativeAI

SYSTEM_PROMPT = (
    "You are fixing an issue in a real repository checked out at /testbed. Use absolute paths "
    "under /testbed with the file tools, and the execute tool to run python or pytest in the "
    "repository's own environment. Change the non-test source files so the issue is resolved; "
    "hidden tests will check the fix. When the fix is done, reply with a short summary."
)


def model(name: str, temperature: float, key_env: str = "AGENT_PLATFORM_API_KEY"):
    """Gemini through Vertex AI express mode (API key read from the environment)."""
    # Gemini 3.6 Flash uses fixed sampling defaults (model page: custom temperature values
    # are ignored), and langchain-google-genai warns on every call. The configured value is
    # the model's own default (1.0) and is recorded in `runs`; the warning adds nothing.
    warnings.filterwarnings("ignore", message=".*uses fixed sampling defaults.*")
    key = os.environ.get(key_env)
    if not key:
        raise RuntimeError(f"{key_env} is not set; export it from .env (never print it)")
    return ChatGoogleGenerativeAI(model=name, vertexai=True, api_key=key, temperature=temperature)


def build(llm, backend, step_limit: int, middleware=(), subagent_middleware=()):
    """The coding agent. `middleware` goes on the root; `subagent_middleware` on the subagent."""
    general_purpose = {**GENERAL_PURPOSE_SUBAGENT, "model": llm, "tools": [],
                       "middleware": list(subagent_middleware)}
    return create_deep_agent(
        model=llm,
        system_prompt=SYSTEM_PROMPT,
        backend=backend,
        subagents=[general_purpose],
        # The same step limit in both arms: root model calls per instance.
        middleware=[ModelCallLimitMiddleware(run_limit=step_limit, exit_behavior="end"),
                    *middleware],
    )
