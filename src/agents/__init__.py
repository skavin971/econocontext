"""Agents built on the EconoContext harness.

Each agent is a directory: its class, the prompts it sends, the tools it adds
beyond read and search, and the fixtures it can be demonstrated on. Adding one
means adding a directory and a line to AGENTS. See README.md.
"""

from .base import BaseAgent
from .coding import CodingAgent
from .research import ResearchAgent

AGENTS = {agent.name: agent for agent in (CodingAgent, ResearchAgent)}

__all__ = ["AGENTS", "BaseAgent", "CodingAgent", "ResearchAgent", "build", "get"]


def get(name):
    """The agent class registered under this name."""
    if name not in AGENTS:
        raise ValueError(f"Unknown agent {name!r}. Available: {', '.join(sorted(AGENTS))}")
    return AGENTS[name]


def build(task, workspace, memory, run_id, timeout):
    """What the harness calls to construct the agent a run asked for."""
    return get(task.adapter)(task, workspace, memory, run_id, timeout)
