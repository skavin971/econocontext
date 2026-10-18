"""CLM's Base as a policy of our harness (step 10c): no context management. The conversation is sent as
it is; with a budget, the harness's budget gate (CLM's rule: one final turn, then the run ends) does the
rest. Without a budget it is the raw arm of steps 5-9."""


class Base:
    name = "base"
    budget = "allowed"

    def start(self, instruction: str, harness) -> None:
        pass

    async def before_call(self, messages: list[dict], harness) -> list[dict]:
        return messages

    def before_tool(self, name: str, args: dict) -> str | None:
        return None

    def after_tool(self, name: str, args: dict, output: str, served: bool, call_id: str | None) -> str:
        return output

    def report(self) -> dict | None:
        return None
