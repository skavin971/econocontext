"""An agent that reads a corpus and answers a question from it, with citations.

Thin on purpose. It has search and read and nothing else, so it can find a
passage and quote it but cannot take a note, follow a claim back to its source,
or assemble a report. See ../README.md for what a real research agent needs.
"""

from pathlib import Path

from econocontext.agent import controls

from ..base import SHARED_TOOLS, BaseAgent
from . import prompts
from .tools import EXTRA_TOOLS


class ResearchAgent(BaseAgent):
    name = "research"
    default_fixture = "corpus"
    fixtures = Path(__file__).parent

    def prompt(self, worker, method):
        template = prompts.CHILD if worker.role == "child" else prompts.ROOT
        return template.format(goal=self.goal)

    def tools(self, worker, method):
        return list(SHARED_TOOLS) + EXTRA_TOOLS + controls(worker, method, self.delegation_tool)

    async def verify(self, result):
        """Checks the answer against the corpus, not that the model sounded sure.

        A cited source plus both figures. Crude, and it is the reason this agent
        cannot yet be pointed at a corpus it was not written for: real research
        verification has to check a claim against the passage it came from.
        """
        if self.task.data:
            return dict(status="unverified", reason="A caller-supplied task needs its own verifier")
        cited = False
        for ref in result.get("evidence", []):
            try:
                evidence = await self.memory.get(ref, self.run_id)
                cited |= evidence.get("source") == "policy.txt"
            except KeyError:
                pass
        answer = result.get("answer", "").lower()
        grounded = cited and "100" in answer and "60" in answer
        return dict(status="verified" if grounded else "failed")
