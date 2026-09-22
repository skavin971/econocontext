"""EconoContext: a cost-aware agent harness.

You give it a prompt, some data and constraints. It decides how each unit of
work is done -- reuse a result it already has, continue a worker that already
holds the context, or hand a scoped child the evidence and take back only the
finding -- and prices those alternatives before choosing.

    import econocontext

    async with econocontext.open() as eco:
        run = await eco.submit(
            prompt="Fix the failing parser test",
            data="./myrepo",
            limits=econocontext.Limits(max_cost=1.00, context_tokens=32000),
        )
        result = await eco.wait(run["id"])

Nothing starts a server or opens a database on import.
"""

from .agent import DomainAdapter
from .config import Config, Pricing
from .contracts import Limits, RunRequest, Task
from .runtime.manager import Manager

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
    "open",
]


class _Opening:
    """The result of `econocontext.open(...)`: awaited, or entered as a context.

    `await econocontext.open()` hands back a started harness the caller closes;
    `async with econocontext.open() as eco` closes it on the way out.
    """

    def __init__(self, *args):
        self.args, self.harness = args, None

    def __await__(self):
        return Harness.open(*self.args).__await__()

    async def __aenter__(self):
        self.harness = await Harness.open(*self.args)
        return self.harness

    async def __aexit__(self, *exc):
        await self.harness.close()


def open(config=None, backend=None, adapter=None):  # noqa: A001 - the module's entry point
    """Start a harness. Shadows the builtin inside this module only."""
    return _Opening(config, backend, adapter)


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
