"""Runnable external measurement integration; no planner adoption required."""

import asyncio
import json
import time
from pathlib import Path

from econocontext.config import Config, Pricing
from econocontext.contracts import RunRequest
from econocontext.memory import MemoryStore
from econocontext.telemetry import Telemetry


async def main():
    config = Config(
        data_dir=Path("data/external"),
        pricing=Pricing(
            revision="synthetic-example",
            input_per_million=1,
            cached_per_million=0.25,
            output_per_million=2,
        ),
    )
    memory = await MemoryStore(config.data_dir).open()
    try:
        run, _ = await memory.create_run(RunRequest(method="react"), config.fingerprint())
        telemetry = Telemetry(memory, config)
        # In an external runner, place begin immediately before each actual call.
        attempt = await telemetry.begin(
            run["id"],
            worker_id="external-session",
            kind="model",
            name="synthetic-external-model",
            request={"messages": []},
        )
        started = time.monotonic()
        # Replace this synthetic response with the external runner's actual result.
        await telemetry.finish(
            attempt,
            duration=time.monotonic() - started,
            status="succeeded",
            response={"answer": "fixture"},
            raw_usage={"prompt_tokens": 12, "completion_tokens": 3},
        )
        print(json.dumps(await telemetry.metrics(run["id"]), indent=2))
    finally:
        await memory.close()


asyncio.run(main())
