"""The boundaries: the method stands alone, measurement never imports it, and agents reach models
only through the gateway. Step 12a: the method keeps its context in the agent DB (agent_db), which the
harness and the baselines never touch, and which holds no cost."""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The method may import the agent DB (its store) and measure/ (the one exact token counter, step 12a).
ALLOWED_THIRD_PARTY = {"yaml", "agent_db", "measure"}
AGENT_DB = ROOT / "AgentDB" / "agent_db"
COST_WORDS = re.compile(r"cost|price|usd|flop|bill", re.I)
# Only gateway/ may name a model provider's host or key (decision of 2026-10-10, scoped to agents/,
# now harness/; baselines/ joined in step 10).
PROVIDER_HOSTS = ("genai.rcac.purdue.edu", "googleapis.com", "anthropic.com", "openai.com")
PROVIDER_KEYS = ("GENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                 "ECONOCONTEXT_ANTHROPIC_KEY", "AGENT_PLATFORM_API_KEY")
# The SDKs' own base-URL settings: a client that read them would have a default upstream.
DEFAULT_UPSTREAM = ("OPENAI_BASE_URL", "OPENAI_API_BASE")


def ours(pattern: str):
    """Our files under harness/ and baselines/: a baseline's own code (baselines/<name>/upstream/) is
    its authors', run through the api_base our wrapper gives it."""
    for folder in ("harness", "baselines"):
        for path in (ROOT / folder).rglob(pattern):
            if path.is_file() and not {"upstream", "__pycache__"} & set(path.relative_to(ROOT).parts):
                yield path


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_core_imports_only_stdlib_yaml_the_agent_db_measure_and_itself():
    for path in (ROOT / "econocontext").rglob("*.py"):
        for name in imports(path):
            ok = name in sys.stdlib_module_names or name in ALLOWED_THIRD_PARTY or name == "econocontext"
            assert ok, f"{path.relative_to(ROOT)} imports {name}"


def test_measure_never_imports_the_method():
    for path in (ROOT / "measure").rglob("*.py"):
        assert "econocontext" not in imports(path), f"{path.relative_to(ROOT)} imports econocontext"


def test_agents_reach_models_only_through_the_gateway():
    # An agent is given an api_base on gateway/ and a placeholder key; it never names a provider's
    # host or reads a provider's key. Every file of ours under harness/ and baselines/ (code, scripts,
    # docs) is checked.
    for path in ours("*"):
        text = path.read_text(errors="ignore")
        named = [name for name in PROVIDER_HOSTS + PROVIDER_KEYS if name in text]
        assert not named, f"{path.relative_to(ROOT)} names {named}"


def test_agents_use_only_the_api_base_they_are_given():
    # Every client an agent builds gets base_url from a variable (the api_base it was given), never a
    # literal URL, and no agent reads the SDK's base-URL settings: there is no default upstream.
    for path in ours("*.py"):
        text = path.read_text()
        named = [name for name in DEFAULT_UPSTREAM if name in text]
        assert not named, f"{path.relative_to(ROOT)} names {named}"
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) in (
                    "OpenAI", "AsyncOpenAI"):
                base = next((k.value for k in node.keywords if k.arg == "base_url"), None)
                assert isinstance(base, ast.Name), f"{path.relative_to(ROOT)}: a client without base_url=<api_base>"


def test_the_harness_imports_no_policy_package_and_names_none():
    # Step 11: a policy is given to the harness by name (the runner's --policy); the harness never
    # depends on the policies, so every arm shares one loop.
    for path in (ROOT / "harness").rglob("*.py"):
        named = imports(path) & {"econocontext", "baselines"}
        assert not named, f"{path.relative_to(ROOT)} imports {named}"
        code = path.read_text()
        assert "econocontext" not in code and "baselines" not in code, f"{path.relative_to(ROOT)} names a policy package"
    assert list((ROOT / "harness").rglob("*.py")), "harness/ is empty"


def test_econocontext_never_imports_the_baselines():
    for path in (ROOT / "econocontext").rglob("*.py"):
        assert "baselines" not in imports(path), f"{path.relative_to(ROOT)} imports baselines"


def test_the_harness_and_the_baselines_never_import_the_agent_db():
    for path in list((ROOT / "harness").rglob("*.py")) + list(ours("*.py")):
        assert "agent_db" not in imports(path), f"{path.relative_to(ROOT)} imports agent_db"


def test_the_agent_db_imports_none_of_our_packages():
    for path in AGENT_DB.rglob("*.py"):
        named = imports(path) & {"econocontext", "harness", "baselines", "measure", "gateway"}
        assert not named, f"{path.relative_to(ROOT)} imports {named}"


def test_the_agent_db_holds_no_cost():
    # Token counts are allowed; no column, record field or operation is a cost, price, dollar, FLOP or bill.
    columns = re.findall(r"^\s+([a-z_0-9]+)\s+[a-z]", (AGENT_DB / "schema.sql").read_text(), re.M)
    assert columns and not [c for c in columns if COST_WORDS.search(c)]
    for path in AGENT_DB.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            name = getattr(node, "name", None) or (node.target.id if isinstance(node, ast.AnnAssign)
                                                   and isinstance(node.target, ast.Name) else None)
            assert not (name and COST_WORDS.search(name)), f"{path.relative_to(ROOT)} defines {name}"
