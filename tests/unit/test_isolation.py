"""The boundaries: the method stands alone, measurement never imports it, and agents reach models
only through the gateway."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_THIRD_PARTY = {"yaml"}
# Only gateway/ may name a model provider's host or key (decision of 2026-10-10, scoped to agents/).
PROVIDER_HOSTS = ("genai.rcac.purdue.edu", "googleapis.com", "anthropic.com", "openai.com")
PROVIDER_KEYS = ("GENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                 "ECONOCONTEXT_ANTHROPIC_KEY", "AGENT_PLATFORM_API_KEY")
# The SDKs' own base-URL settings: a client that read them would have a default upstream.
DEFAULT_UPSTREAM = ("OPENAI_BASE_URL", "OPENAI_API_BASE")


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_core_imports_only_stdlib_yaml_and_itself():
    for path in (ROOT / "econocontext").rglob("*.py"):
        for name in imports(path):
            ok = name in sys.stdlib_module_names or name in ALLOWED_THIRD_PARTY or name == "econocontext"
            assert ok, f"{path.relative_to(ROOT)} imports {name}"


def test_measure_never_imports_the_method():
    for path in (ROOT / "measure").rglob("*.py"):
        assert "econocontext" not in imports(path), f"{path.relative_to(ROOT)} imports econocontext"


def test_agents_reach_models_only_through_the_gateway():
    # An agent is given an api_base on gateway/ and a placeholder key; it never names a provider's
    # host or reads a provider's key.
    for path in (ROOT / "agents").rglob("*.py"):
        text = path.read_text()
        named = [name for name in PROVIDER_HOSTS + PROVIDER_KEYS if name in text]
        assert not named, f"{path.relative_to(ROOT)} names {named}"


def test_agents_use_only_the_api_base_they_are_given():
    # Every client an agent builds gets base_url from a variable (the api_base it was given), never a
    # literal URL, and no agent reads the SDK's base-URL settings: there is no default upstream.
    for path in (ROOT / "agents").rglob("*.py"):
        text = path.read_text()
        named = [name for name in DEFAULT_UPSTREAM if name in text]
        assert not named, f"{path.relative_to(ROOT)} names {named}"
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) in (
                    "OpenAI", "AsyncOpenAI"):
                base = next((k.value for k in node.keywords if k.arg == "base_url"), None)
                assert isinstance(base, ast.Name), f"{path.relative_to(ROOT)}: a client without base_url=<api_base>"
