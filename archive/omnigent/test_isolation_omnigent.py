"""The Omnigent track's boundary rule, moved verbatim from tests/unit/test_isolation.py (step 2).

Archived with the track: its paths point at the old layout, so it does not run in place. At tag
econo-jev-final it runs from tests/unit/test_isolation.py.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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


def test_only_the_session_runner_imports_omnigent():
    # The Omnigent layer reads Omnigent's events as plain dicts (policy.py) and speaks HTTP
    # (gateway.py); only harness/session.py uses Omnigent's own code, to start sessions.
    for folder in ("omnigent_layer/src", "harness", "benchmarks"):
        for path in (ROOT / folder).rglob("*.py"):
            if path.relative_to(ROOT).as_posix() == "harness/session.py":
                continue
            assert not imports(path) & OMNIGENT, f"{path.relative_to(ROOT)} imports Omnigent"
