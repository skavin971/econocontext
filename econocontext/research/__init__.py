"""Research recording, independent of the operational AgentDB.

Only offline analysis reads these records. Nothing in planning, retrieval, budgets,
or worker placement reads this database.
"""

from .recorder import ResearchRecorder

__all__ = ["ResearchRecorder"]
