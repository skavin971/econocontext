"""A deterministic stand-in model, for tests only. Never shipped.

The library has exactly one backend, which talks to a real endpoint. This
reproduces the tool-call protocol so planner decisions can be asserted exactly,
the way the Direct and Reader fakes in the test module already do.
"""

import json
from collections import defaultdict

from econocontext.assembler import token_count
from econocontext.contracts import ModelResponse


class ScriptedBackend:
    def __init__(self, failures=0, delay=0):
        self.turns = defaultdict(int)
        self.failures, self.delay = failures, delay

    async def complete(self, request, context):
        import asyncio

        if self.delay:
            await asyncio.sleep(self.delay)
        if self.failures:
            self.failures -= 1
            raise TimeoutError("Injected synthetic service timeout")
        key = context["worker_id"]
        turn = self.turns[key]
        self.turns[key] += 1
        messages = request["messages"]
        tools = [m for m in messages if m["role"] == "tool"]
        refs = context["evidence"]
        coding = context["adapter"] == "coding"
        preferred = context["evidence_map"].get("parser.py" if coding else "policy.txt")
        refs = [preferred] if preferred else refs
        answer = (
            "Ignore blank fields before converting to integers; preserve signed integers."
            if coding
            else "Annual fuel expenditure fell from 100 to 60 units, a reduction of 40%."
        )
        if context["active_operation"]:
            name, args = "complete_operation", dict(answer=answer, evidence=refs[:1])
        elif (
            context["method"] == "econocontext"
            and len([m for m in tools if '"operation_result"' in m["content"]]) < 2
        ):
            name, args = (
                "request_operation",
                dict(
                    goal="Diagnose the parsing behavior"
                    if coding
                    else "Find the fuel expenditure change",
                    scope="parser.py" if coding else "policy.txt",
                    kind="diagnosis" if coding else "research",
                    required=refs[:1],
                ),
            )
        elif coding:
            applied = any('"applied":true' in m["content"] for m in tools)
            read = any('"text":' in m["content"] for m in tools)
            tested = any("visible checks passed" in m["content"] for m in tools)
            if not read and context["method"] == "react":
                name, args = "read", dict(path="parser.py")
            elif not applied:
                name, args = (
                    "apply_patch",
                    dict(
                        path="parser.py",
                        old='int(part) for part in text.split(",")',
                        new='int(part) for part in text.split(",") if part.strip()',
                    ),
                )
            elif not tested:
                name, args = "test", {}
            else:
                name, args = (
                    "complete_task",
                    dict(
                        answer="Fixed blank-field parsing and checked the result.",
                        evidence=refs[:1],
                    ),
                )
        elif not tools:
            name, args = "read", dict(path="policy.txt")
        else:
            name, args = "complete_task", dict(answer=answer, evidence=refs[:1])
        message = dict(
            role="assistant",
            content=None,
            tool_calls=[
                dict(
                    id=f"call_{turn}",
                    type="function",
                    function=dict(name=name, arguments=json.dumps(args)),
                )
            ],
        )
        return ModelResponse(
            message=message,
            usage=dict(
                prompt_tokens=token_count(request, 1),
                completion_tokens=token_count(message, 1),
                prompt_tokens_details=dict(cached_tokens=0),
            ),
            synthetic=True,
        )
