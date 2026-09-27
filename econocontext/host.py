"""What a host must provide for EconoContext to act. Protocols only.

Why it exists: the core must stay harness-agnostic. Anything that touches the
host's machinery (writing a file the agent can reopen, knowing what the host can
do) goes through these protocols, which each adapter implements.
What it must never do: import a host framework.
"""

from dataclasses import dataclass
from typing import Protocol

from .types import Segment


@dataclass
class HostCapabilities:
    """What this host can carry out. A missing capability removes the operator from
    the search space; it is never approximated."""
    pointer: bool = False             # can store full content somewhere the agent can reopen
    answer_from_store: bool = False   # can return a stored tool output instead of running the tool
    reuse_result: bool = False        # can return a stored delegated result instead of delegating
    edit_request: bool = False        # can replace the messages of a model request


class PointerStore(Protocol):
    def materialize(self, segment: Segment) -> str:
        """Save the segment's full text where the agent can reopen it; return that path."""
        ...


class Host(Protocol):
    capabilities: HostCapabilities
    pointer_store: PointerStore | None
