"""EconoContext: a cost-aware agent harness.

You give it a prompt, some data and constraints. It decides how each unit of
work is done -- reuse a result it already has, continue a worker that already
holds the context, or hand a scoped child the evidence and take back only the
finding -- and prices those alternatives before choosing.

    from econocontext import Harness, Config, Limits

    harness = await Harness.open(Config.from_env())
    try:
        run = await harness.submit(
            prompt="Fix the failing parser test",
            data="./myrepo",
            limits=Limits(max_cost=1.00, context_tokens=32000),
        )
        result = await harness.wait(run["id"])
    finally:
        await harness.close()

Nothing starts a server or opens a database on import.
"""

from .config import Config, Pricing
from .contracts import Limits, RunRequest, Task
from .domain import DomainAdapter
from .manager import Manager

__version__ = "0.1.0"

__all__ = [
    "Config",
    "DomainAdapter",
    "Harness",
    "Limits",
    "Manager",
    "Pricing",
    "RunRequest",
    "Task",
    "__version__",
]


class Harness(Manager):
    """The library entry point: a task is a prompt, some data, and constraints."""

    @classmethod
    async def open(cls, config=None, backend=None, adapter=None):
        """Start a harness. `adapter` builds the domain agent for each run;
        the default bundle is used when none is given."""
        if adapter is None:
            from agents import build as adapter
        return await cls(config or Config.from_env(), backend, adapter).start()

    async def submit(self, request=None, **task):
        """Accept either a built RunRequest or prompt/data/limits directly."""
        if request is not None:
            return await super().submit(request)
        method = task.pop("method", "econocontext")
        limits = task.pop("limits", None)
        idempotency_key = task.pop("idempotency_key", None)
        return await super().submit(
            RunRequest(
                task=Task(**task),
                method=method,
                limits=limits or Limits(),
                idempotency_key=idempotency_key,
            )
        )
