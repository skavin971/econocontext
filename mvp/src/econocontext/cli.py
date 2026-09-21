import argparse
import asyncio
import json
from pathlib import Path

from .config import Config
from .contracts import RunRequest, Task
from .manager import Manager
from .memory import MemoryStore
from .telemetry import Telemetry, build_profiles


def output(data, path=None):
    text = json.dumps(data, indent=2)
    if path:
        Path(path).write_text(text + "\n")
    else:
        print(text)


async def run(args):
    config = Config.from_env()
    if args.data_dir:
        config.data_dir = Path(args.data_dir)
    if args.command in ("run", "demo"):
        manager = await Manager(config).start()
        try:
            tasks = (
                [(a, m) for a in ("coding", "research") for m in ("react", "econocontext")]
                if args.command == "demo"
                else [(args.adapter, args.method)]
            )
            reports = []
            for adapter, method in tasks:
                task = Task(adapter=adapter)
                if args.command == "run":
                    task = (
                        Task.model_validate_json(Path(args.task).read_text()) if args.task else task
                    )
                run = await manager.submit(RunRequest(task=task, method=method))
                final = await manager.wait(run["id"])
                reports.append(dict(run=final, metrics=await manager.telemetry.metrics(run["id"])))
            output(reports)
            if any(r["run"]["status"] != "succeeded" for r in reports):
                raise SystemExit(1)
        finally:
            await manager.close()
    else:
        memory = await MemoryStore(config.data_dir).open()
        try:
            if args.command == "profiles":
                output(await build_profiles(memory, args.run_ids), args.output)
            elif args.command == "reconstruct":
                from .assembler import Assembler

                output(Assembler(memory, config).reconstruct(args.manifest), args.output)
            else:
                metrics = await Telemetry(memory, config).metrics(args.run_id)
                output(
                    dict(
                        run=await memory.run(args.run_id),
                        metrics=metrics,
                        trace=await memory.all_events(args.run_id),
                    ),
                    args.output,
                )
        finally:
            await memory.close()


def main():
    parser = argparse.ArgumentParser(prog="econocontext")
    parser.add_argument("--data-dir")
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("run")
    runner.add_argument("--adapter", choices=["coding", "research"], default="coding")
    runner.add_argument("--method", choices=["react", "econocontext"], default="econocontext")
    runner.add_argument(
        "--task", help="JSON Task file, including optional pinned local repository or corpus"
    )
    sub.add_parser("demo")
    profiles = sub.add_parser("profiles")
    profiles.add_argument("run_ids", nargs="+")
    profiles.add_argument("--output", required=True)
    export = sub.add_parser("export")
    export.add_argument("run_id")
    export.add_argument("--output")
    reconstruct = sub.add_parser("reconstruct")
    reconstruct.add_argument("manifest")
    reconstruct.add_argument("--output")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
