"""Check each SWE fixture's verifier before any model is paid to solve it.

For every fixture: the untouched repository must fail, and the upstream fix
must pass. A task where either is wrong would score agents on noise.
"""

import argparse
import asyncio
import tempfile
import time
from pathlib import Path

from agents.swe import SweAgent
from econocontext.contracts import Task
from econocontext.store.memory import MemoryStore

FIXTURES = Path(__file__).resolve().parents[2] / "src" / "agents" / "swe" / "fixtures"


async def check(instance):
    with tempfile.TemporaryDirectory() as root:
        memory = MemoryStore(Path(root) / "data")
        await memory.open()
        try:
            task = Task(adapter="swe", fixture=instance)
            agent = SweAgent(task, Path(root) / "ws", memory, f"verify-{instance}", 120)
            await agent.prepare()
            try:
                started = time.monotonic()
                empty = await agent.check("")
                reference = (FIXTURES / instance / "reference.diff").read_text()
                fixed = await agent.check(reference)
                seconds = time.monotonic() - started
            finally:
                await agent.close()
        finally:
            await memory.close()
    ok = empty["status"] == "failed" and fixed["status"] == "verified"
    print(
        f"{'OK  ' if ok else 'BAD '} {instance}: untouched={empty['status']} "
        f"(f2p failing {len(empty['fail_to_pass']['failing'])}/{empty['fail_to_pass']['total']}), "
        f"reference={fixed['status']} (p2p failing {len(fixed['pass_to_pass']['failing'])}"
        f"/{fixed['pass_to_pass']['total']}) in {seconds:.0f}s"
    )
    if not ok:
        print("   ", fixed)
    return ok


async def main(instances):
    results = [await check(i) for i in instances]
    raise SystemExit(0 if all(results) else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("instances", nargs="*")
    args = parser.parse_args()
    names = args.instances or sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())
    asyncio.run(main(names))
