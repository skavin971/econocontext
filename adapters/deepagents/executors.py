"""Deep Agents' side of host.py: what EconoContext asks the host to carry out.

Why it exists: the core cannot touch the host's machinery. Writing a pointer file
the agent can reopen, and finding which files a shell command changed, happen
here, through the Deep Agents backend the agent itself uses.
What it must never do: decide anything, or bypass the backend (the agent must be
able to reopen a pointer with its own file tool).
"""

import logging
import shlex

from econocontext.host import HostCapabilities
from econocontext.types import Segment

log = logging.getLogger("econocontext")
POINTER_DIR = "/tmp/econocontext"


class DeepAgentsHost:
    """Implements econocontext.host.Host for a Deep Agents agent and its backend."""

    def __init__(self, backend, workspace_root: str = "/testbed"):
        self.backend = backend
        self.root = workspace_root.rstrip("/")
        writable = hasattr(backend, "upload_files") or hasattr(backend, "write")
        self.capabilities = HostCapabilities(pointer=writable, answer_from_store=True,
                                             reuse_result=True, edit_request=True)
        self.pointer_store = self
        self.known: dict[str, str] | None = None  # path -> git blob hash, after the first check

    # -- PointerStore --------------------------------------------------------------

    def materialize(self, segment: Segment) -> str:
        """Write the full text where `read_file` can reopen it; return the path."""
        path = f"{POINTER_DIR}/{segment.id[:16]}.txt"
        data = segment.text.encode()
        if hasattr(self.backend, "upload_files"):
            [response] = self.backend.upload_files([(path, data)])
            if response.error:
                raise RuntimeError(f"pointer write failed: {response.error}")
        else:
            self.backend.write(path, segment.text)
        return path

    # -- the write barrier after a shell command ----------------------------------------

    def changed_paths(self) -> list[str] | None:
        """Paths whose contents changed since the last check, via `git status` in the
        workspace. Returns None if git cannot answer: the caller then bumps the whole
        workspace epoch instead (conservative fallback)."""
        if not hasattr(self.backend, "execute"):
            return None
        root = shlex.quote(self.root)
        status = self.backend.execute(f"git -C {root} status --porcelain --untracked-files=all")
        if status.exit_code != 0:
            return None
        paths = [line[3:].split(" -> ")[-1].strip() for line in status.output.splitlines()
                 if len(line) > 3]
        current: dict[str, str] = {}
        if paths:
            quoted = " ".join(shlex.quote(p) for p in paths)
            # A deleted path makes hash-object fail for it; --stdin-paths would too, so ask
            # per path and treat a missing file as its own state.
            hashes = self.backend.execute(
                f"cd {root} && for f in {quoted}; do git hash-object -- \"$f\" 2>/dev/null "
                f"|| echo deleted; done")
            if hashes.exit_code != 0:
                return None
            current = dict(zip(paths, hashes.output.split()))
        previous = self.known or {}
        changed = [p for p, h in current.items() if previous.get(p) != h]
        changed += [p for p in previous if p not in current]  # reverted to HEAD
        self.known = current
        return [f"{self.root}/{p}" for p in changed]
