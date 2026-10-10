"""Fail if a secret from .env appears in what is about to be committed or pushed.

Why it exists: every step's commit is checked before it is made, and the branch before it is pushed.
A secret is any .env value of 12 or more characters that is not just an identifier. An UPPER_CASE
variable name, such as AGENT_PLATFORM_API_KEY, names where a key lives; it is not a key. The check
reads added lines only (`git diff --cached`, or `git log -p` over a range, binary files as text). It
prints file names and counts, never a value, and exits 1 on any hit.

Run: .venv/bin/python scripts/check_secrets.py                          (staged changes)
     .venv/bin/python scripts/check_secrets.py --range econo-jev-final..HEAD   (every commit in a range)
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


def secrets() -> tuple[list[str], int]:
    values, names = [], 0
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        value = line.split("=", 1)[1].strip().strip('"').strip("'")
        if len(value) < 12:
            continue
        if NAME.match(value):
            names += 1
            continue
        values.append(value)
    return values, names


def added_lines(git_range: str | None) -> dict[str, list[str]]:
    """File -> its added lines, from the staged diff or from every commit in the range."""
    command = (["git", "log", "-p", "--text", "-U0", "--format=", git_range] if git_range
               else ["git", "diff", "--cached", "--text", "-U0"])
    out = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, errors="replace", check=True).stdout
    files: dict[str, list[str]] = {}
    current = None
    for line in out.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
        elif current and line.startswith("+"):
            files.setdefault(current, []).append(line[1:])
    return files


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--range", help="check every commit in this git range instead of the staged changes")
    a = p.parse_args()
    values, names = secrets()
    files = added_lines(a.range)
    hits = {f: sum(text.count(v) for v in values for text in lines) for f, lines in files.items()}
    hits = {f: n for f, n in hits.items() if n}
    where = f"commits {a.range}" if a.range else "staged changes"
    print(f"secrets check ({where}): {len(values)} .env values checked ({names} variable names skipped) "
          f"against added lines in {len(files)} files; files with a secret: {len(hits)}")
    for f, n in sorted(hits.items()):
        print(f"  {f}: {n} occurrence(s)")
    sys.exit(1 if hits else 0)


if __name__ == "__main__":
    main()
