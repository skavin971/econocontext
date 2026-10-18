"""CLM's Summary baseline as a policy of our harness (step 10d; CLM: "the summary harness uses the Codex
summarization prompts and compacts at 75% of the budget").

It compacts as Codex 0.146.0 does (codex-rs/core/src/compact.rs, commit e363b08; the prompts here are
Codex's, byte for byte, Apache-2.0): when the prompt reaches 75% of the budget (24,000 at 32,000; not
75% of budget - 2,048), one call (kind summary) sends the whole conversation plus Codex's
SUMMARIZATION_PROMPT as a user message, with no tools (Prompt::default()). Codex then keeps the recent
real user messages (up to 20,000 tokens) and appends "{SUMMARY_PREFIX}\\n{summary}"; our one real user
message is the task (the nudges and the final-turn notice are the harness's), so the conversation
becomes [system, task, summary]. The harness's budget gate applies after it, as for Base. It needs a
budget.
"""

from pathlib import Path

from harness.interface import stored_text

HERE = Path(__file__).resolve().parent
CODEX_COMPACT = stored_text(HERE / "codex_compact.md")                # Codex's SUMMARIZATION_PROMPT
CODEX_SUMMARY_PREFIX = stored_text(HERE / "codex_summary_prefix.md")  # Codex's SUMMARY_PREFIX
SUMMARY_AT = 0.75     # CLM: 75% of the budget (Table 4: "about 24.6K of 32,768 tokens"), not of budget - 2,048
CODEX_KEEP_USER_TOKENS = 20_000   # Codex keeps recent real user messages up to this many tokens


class Summary:
    name = "summary"
    budget = "required"

    def __init__(self):
        self.compactions: list[dict] = []

    def start(self, instruction: str, harness) -> None:
        pass

    async def before_call(self, messages: list[dict], harness) -> list[dict]:
        tokens = harness.count(messages)
        if tokens < SUMMARY_AT * harness.budget:
            return messages
        task = messages[1]
        if len(task["content"].encode()) > 4 * CODEX_KEEP_USER_TOKENS:
            raise RuntimeError("the task is over Codex's 20,000-token user-message cap; truncating it is not implemented")
        request = messages + [{"role": "user", "content": CODEX_COMPACT}]
        call_tokens = harness.count(request, None)
        reply = await harness.complete(request, kind="summary", tools=None)
        choice = reply.choices[0] if reply.choices else None
        summary = (choice.message.content if choice is not None and choice.message is not None else None) or ""
        usage = reply.usage.model_dump() if reply.usage else {}
        messages[:] = [messages[0], task, {"role": "user", "content": f"{CODEX_SUMMARY_PREFIX}\n{summary}"}]
        after = harness.count(messages)
        self.compactions.append({
            "summary_call": len(harness.calls), "P_before": tokens, "P_call": call_tokens,
            "G": usage.get("completion_tokens"), "capped": (usage.get("completion_tokens") or 0) >= harness.max_tokens,
            "finish_reason": choice.finish_reason if choice is not None else None, "summary_chars": len(summary),
            "P_after": after, "P_after_share_of_budget": round(after / harness.budget, 4)})
        return messages

    def before_tool(self, name: str, args: dict) -> str | None:
        return None

    def after_tool(self, name: str, args: dict, output: str, served: bool, call_id: str | None) -> str:
        return output

    def report(self) -> dict | None:
        return {"compactions": self.compactions}
