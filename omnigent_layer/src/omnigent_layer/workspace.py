"""The write barrier's eyes: which files in the run's work directory changed.

Why it exists: stored tool results are reused only while every file they read is
unchanged. After any tool that can write (edit, write, shell, testbed_shell), this
compares the work directory with what it saw last, using git, so the engine can bump
exactly the paths that changed.
What it must never do: modify the work directory.
"""

import subprocess
from pathlib import Path


class Workspace:
    def __init__(self, root: str):
        self.root = Path(root)
        self.known: dict[str, str] | None = None  # path -> git blob hash, as last seen

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True,
                              text=True, check=True, timeout=60).stdout

    def _state(self) -> dict[str, str]:
        """Every path git reports as changed from HEAD (tracked or not), with its hash."""
        paths = [line[3:] for line in self._git("status", "--porcelain", "--untracked-files=all")
                 .splitlines() if len(line) > 3]
        paths = [p.split(" -> ")[-1].strip('"') for p in paths]
        present = [p for p in paths if (self.root / p).is_file()]
        hashes = self._git("hash-object", "--", *present).split() if present else []
        state = dict(zip(present, hashes))
        state.update({p: "deleted" for p in paths if p not in state})
        return state

    def snapshot(self) -> None:
        try:
            self.known = self._state()
        except Exception:
            self.known = None

    def changed(self) -> list[str] | None:
        """Paths whose content changed since the last call. None: git could not tell."""
        try:
            state = self._state()
        except Exception:
            return None
        before = self.known or {}
        self.known = state
        return sorted(p for p in set(state) | set(before) if state.get(p) != before.get(p))
