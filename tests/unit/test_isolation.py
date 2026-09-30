"""The boundaries: the optimizer stands alone, and only one file imports Omnigent."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_THIRD_PARTY = {"yaml"}
OMNIGENT = {"omnigent", "omnigent_client"}


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


def test_only_the_session_runner_imports_omnigent():
    # The Omnigent layer reads Omnigent's events as plain dicts (policy.py) and speaks HTTP
    # (gateway.py); only harness/session.py uses Omnigent's own code, to start sessions.
    for folder in ("omnigent_layer/src", "harness", "benchmarks"):
        for path in (ROOT / folder).rglob("*.py"):
            if path.relative_to(ROOT).as_posix() == "harness/session.py":
                continue
            assert not imports(path) & OMNIGENT, f"{path.relative_to(ROOT)} imports Omnigent"
