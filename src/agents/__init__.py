"""Domain agents that use the EconoContext harness."""

from .local import LocalAdapter

__all__ = ["LocalAdapter", "build"]


def build(task, workspace, memory, run_id, timeout):
    """The default agent bundle: one adapter covering both domains."""
    return LocalAdapter(task, workspace, memory, run_id, timeout)
