"""EconoContext as a policy of our harness (harness/, step 11): the Owner, with no rule change, behind the
hooks every policy has. The harness imports this by name; this module imports nothing from harness/ or
baselines/.

Econo: mode jev (Jev answers the rules' questions) or prior (fixed guesses). EconoContext has no budget
mechanism yet, so it refuses a budget. Its state is in the agent DB (step 12a): Postgres at AGENTDB_DSN
(the environment, else .env; `docker compose up -d` starts one), one run id per trial, used once. Its
decisions go to decisions.jsonl beside trajectory.json.
Directed: a stub for the next design; it refuses to run.
"""

import os
import uuid
from pathlib import Path

from agent_db import PostgresAgentDB

from .owner import Owner

ENV = Path(__file__).resolve().parents[1] / ".env"


def agentdb_dsn() -> str:
    """AGENTDB_DSN from the environment, else from .env (only that line is read)."""
    if os.environ.get("AGENTDB_DSN"):
        return os.environ["AGENTDB_DSN"]
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "AGENTDB_DSN" and value.strip():
                return value.strip().strip('"').strip("'")
    raise RuntimeError("EconoContext needs its agent DB: set AGENTDB_DSN (see docker-compose.yml)")


class Econo:
    name = "econo"
    budget = "refused"
    budget_refusal = "EconoContext has no budget mechanism yet"

    def __init__(self, mode: str = "jev", force=None, cache="on", dsn: str | None = None, count=None):
        """dsn: the agent DB (AGENTDB_DSN by default). count: text -> tokens, for tests only (the served
        model's tokenizer by default)."""
        self.mode, self.force = mode, set(force or [])     # force: spikes only, rules applied whenever they apply
        self.cache = str(cache).lower() not in ("off", "false", "0", "none")
        self.dsn, self.count = dsn, count
        self.owner = None

    def start(self, instruction: str, harness) -> None:
        store = PostgresAgentDB(self.dsn or agentdb_dsn())
        run = f"{harness.run_id or 'run'}:{uuid.uuid4().hex[:12]}"
        self.owner = Owner(run, self.mode, store, Path(harness.logs_dir) / "decisions.jsonl", force=self.force,
                           cache=self.cache, model=harness.model, count=self.count)
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
