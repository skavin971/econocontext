"""The agent tree and every agent's window.

Why it exists: EconoContext must know which agents and subagents exist and what
each one's window holds, to price plans and to find content it already has.
Design: the tree and windows live in memory for speed and are written through to
the Agent DB (`agents`, `segments`, `window_entries`) on every change, so the DB
is always a complete record. An agent's window is, in the DB, a query: its
entries with in_window = 1, ordered by position.
What it must never do: decide anything; it only records what is.
"""

from ..store.db import AgentDB
from ..types import AgentNode, AgentStatus, Segment


class Registry:
    def __init__(self, db: AgentDB, run_id: str):
        self.db, self.run_id = db, run_id
        self.agents: dict[str, AgentNode] = {}
        self.windows: dict[str, list[Segment]] = {}

    def ensure_agent(self, agent_id: str, parent_id: str | None = None,
                     subagent_type: str | None = None,
                     window_max_tokens: int | None = None) -> AgentNode:
        node = self.agents.get(agent_id)
        if node is None:
            node = AgentNode(agent_id=agent_id, parent_id=parent_id, subagent_type=subagent_type,
                             window_max_tokens=window_max_tokens)
            self.agents[agent_id] = node
            self.db.upsert_agent(self.run_id, node)
        return node

    def set_status(self, agent_id: str, status: AgentStatus) -> None:
        node = self.ensure_agent(agent_id)
        node.status = status
        self.db.upsert_agent(self.run_id, node)

    def sync_window(self, agent_id: str, segments: list[Segment]) -> list[Segment]:
        """Record that `segments`, in this order, are the agent's current window."""
        self.ensure_agent(agent_id)
        for position, segment in enumerate(segments):
            segment.position = position
            segment.in_window = True
            self.db.add_segment(segment)  # write-once; an existing identity is left alone
        self.db.set_window(agent_id, segments)
        self.windows[agent_id] = list(segments)
        return segments

    def add_segment(self, segment: Segment) -> None:
        """Store content that is not (yet) part of a synced window, e.g. a tool result."""
        self.ensure_agent(segment.agent_id)
        self.db.add_segment(segment)

    def window(self, agent_id: str) -> list[Segment]:
        return self.windows.get(agent_id, [])

    def record_read(self, agent_id: str, read_set: dict[str, str]) -> None:
        self.ensure_agent(agent_id).read_set.update(read_set)

    def mark_side_effect(self, agent_id: str) -> None:
        self.ensure_agent(agent_id).side_effect = True

    def turn_end(self, agent_id: str) -> None:
        self.ensure_agent(agent_id).turns += 1
