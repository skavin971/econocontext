"""EconoContext as a policy of our harness (harness/, step 11): the Owner, with no rule change, behind the
hooks every policy has. The harness imports this by name; this module imports nothing outside
econocontext/.

Econo: mode jev (Jev answers the rules' questions) or prior (fixed guesses). EconoContext has no budget
mechanism yet, so it refuses a budget.
Directed: a stub for the next design; it refuses to run.
"""

from pathlib import Path

from .owner import Owner


class Econo:
    name = "econo"
    budget = "refused"
    budget_refusal = "EconoContext has no budget mechanism yet"

    def __init__(self, mode: str = "jev", force=None, cache="on"):
        self.mode, self.force = mode, set(force or [])     # force: spikes only, rules applied whenever they apply
        self.cache = str(cache).lower() not in ("off", "false", "0", "none")
        self.owner = None

    def start(self, instruction: str, harness) -> None:
        self.owner = Owner(harness.run_id, self.mode, Path(harness.logs_dir) / "session.sqlite3",
                           force=self.force, cache=self.cache)
        self.owner.start(instruction)

    async def before_call(self, messages: list[dict], harness) -> list[dict]:
        return await self.owner.before_call(messages, harness.calls,
                                            lambda request: harness.complete(request, kind="summary"))

    def before_tool(self, name: str, args: dict) -> str | None:
        return self.owner.before_tool(name, args)

    def after_tool(self, name: str, args: dict, output: str, served: bool, call_id: str | None) -> str:
        return self.owner.after_tool(name, args, output, served, call_id)

    def report(self) -> dict | None:
        return self.owner.report() if self.owner else None


class Directed:
    """Not built yet: it refuses to run, so no run can use it by mistake."""
    name = "directed"

    def __init__(self, **kwargs):
        raise NotImplementedError("the directed policy is not built yet")
