"""Spike (d): can a driver interrupt Claude Code mid-turn, send /compact with instructions, and continue?

One session through claude_agent_sdk, the same CLI (2.1.286), settings and gateway as session A.
Writes every message to sessionD.messages.jsonl; prints a short verdict.
"""

import asyncio
import dataclasses
import json
import sys
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

D = Path(__file__).parent
OUT = D / "sessionD.messages.jsonl"


def dump(message) -> dict:
    try:
        data = dataclasses.asdict(message)
    except TypeError:
        data = {"repr": repr(message)}
    return {"kind": type(message).__name__, **data}


async def drain(client, log, label: str, on_tool_use=None) -> ResultMessage | None:
    async for message in client.receive_response():
        record = dump(message)
        record["step"] = label
        log.write(json.dumps(record, default=str) + "\n")
        log.flush()
        if on_tool_use and record["kind"] == "AssistantMessage" and "ToolUseBlock" in repr(message):
            on_tool_use()
        if isinstance(message, ResultMessage):
            return message
    return None


async def main() -> None:
    options = ClaudeAgentOptions(
        cli_path="/opt/homebrew/bin/claude", settings=str(D / "settings.host.json"), cwd=str(D / "workA"),
        env={"CLAUDE_CONFIG_DIR": str(D / "cfgD"),
             "ANTHROPIC_BASE_URL": "http://127.0.0.1:8787/run/spike1:baseline:host/anthropic"},
        max_turns=8)
    (D / "cfgD").mkdir(exist_ok=True)
    results = {}
    with OUT.open("w") as log:
        async with ClaudeSDKClient(options=options) as client:
            # 1. A slow loop, interrupted mid-run by the driver.
            await client.query("Remember the code word BLUE-42. Then run this Bash command and wait for it: "
                               "for i in 1 2 3 4 5 6 7 8 9 10; do echo step $i; sleep 2; done   "
                               "Then reply DONE.")
            started = asyncio.Event()
            drain_task = asyncio.create_task(drain(client, log, "1-loop", started.set))
            await asyncio.wait_for(started.wait(), timeout=120)
            await asyncio.sleep(5)
            await client.interrupt()
            results["interrupt"] = await asyncio.wait_for(drain_task, timeout=120)
            # 2. Compact with our instructions.
            await client.query("/compact Keep only the code word and that a loop was interrupted.")
            results["compact"] = await drain(client, log, "2-compact")
            # 3. Continue: does the model still know what we told it to keep?
            await client.query("What is the code word? Answer in one line, then run: echo two")
            results["continue"] = await drain(client, log, "3-continue")
    for step, result in results.items():
        text = (getattr(result, "result", None) or "")[:200].replace("\n", " ")
        print(f"{step:9s} subtype={getattr(result, 'subtype', None)} error={getattr(result, 'is_error', None)} "
              f"cost={getattr(result, 'total_cost_usd', None)} result={text!r}")


if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(0)
