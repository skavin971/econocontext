"""The core imports nothing outside the core; hosts know nothing about EconoContext."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_THIRD_PARTY = {"yaml"}


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


def test_hosts_know_nothing_about_econocontext_except_the_composition_root():
    for path in (ROOT / "hosts").rglob("*.py"):
        if path.name == "run.py":
            continue  # the composition root installs the adapter
        forbidden = imports(path) & {"econocontext", "adapters"}
        assert not forbidden, f"{path.relative_to(ROOT)} imports {forbidden}"
