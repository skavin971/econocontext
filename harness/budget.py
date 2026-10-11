"""CLM's budget gate (step 10c), the same for every policy that runs with a budget.

CLM's released code (clm/clm_harness/utils/budget.py, BudgetController.evaluate, with no rollback
retries): when the prompt is over budget - 2,048, the model gets ONE final turn with CLM's notice
appended (prompts/clm_final_turn.txt, verbatim); still over at the next check, the run ends and the
container is graded as it stands. Back under the limit re-arms the final turn.
"""

from pathlib import Path

from .interface import stored_text

RESERVE = 2048        # CLM's context_budget_reserve_tokens: the limit is budget - 2,048
FINAL_TURN = stored_text(Path(__file__).resolve().parent / "prompts" / "clm_final_turn.txt")


class BudgetGate:
    def __init__(self, budget: int):
        self.budget = budget
        self.finalized = False            # the final turn is granted and not re-armed
        self.final_turns: list[dict] = []
        self.end_P = None

    @property
    def limit(self) -> int:
        return self.budget - RESERVE

    def allows(self, sent: list[dict], tokens: int, next_call: int) -> bool:
        """False: stop before this call. Over the limit the first time, CLM's notice is appended to `sent`
        (the conversation itself, for every policy that runs with a budget)."""
        if tokens <= self.limit:
            self.finalized = False
            return True
        if self.finalized:
            self.end_P = tokens
            return False
        self.finalized = True
        self.final_turns.append({"before_call": next_call, "P": tokens})
        sent.append({"role": "user", "content": FINAL_TURN.format(tokens=tokens, budget=self.budget)})
        return True
