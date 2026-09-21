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


def stepper(memory, config, limits):
    """Narrate each event as it is written, pausing so one run can be walked."""
    from .assembler import Assembler
    from .explain import Narrator

    narrator = Narrator(
        lambda ref: memory.artifacts.read_json(ref) if ref else None,
        limits,
        Assembler(memory, config).reconstruct,
    )
    mode = {"at": "step"}

    def observe(event):
        if mode["at"] == "quiet":
            return
        lines = narrator.render_event(event)
        if not lines:
            return
        print("\n".join(lines), flush=True)
        if mode["at"] != "step":
            return
        try:
            answer = input("   [enter] next    [c] run on    [q] stop narrating > ").strip().lower()
            print()
        except (EOFError, KeyboardInterrupt):
            mode["at"] = "run"
            return
        if answer == "c":
            mode["at"] = "run"
        elif answer == "q":
            mode["at"] = "quiet"

    return observe


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
            overrides = {
                field: getattr(args, field, None)
                for field in (
                    "max_cost",
                    "max_attempts",
                    "deadline",
                    "output_tokens",
                    "context_tokens",
                    "max_children",
                    "retries",
                )
                if getattr(args, field, None) is not None
            }
            reports = []
            for adapter, method in tasks:
                task = Task(adapter=adapter)
                if args.command == "run":
                    task = (
                        Task.model_validate_json(Path(args.task).read_text()) if args.task else task
                    )
                request = RunRequest(task=task, method=method)
                if overrides:
                    request = request.model_copy(
                        update=dict(limits=request.limits.model_copy(update=overrides))
                    )
                if getattr(args, "step", False):
                    manager.memory.observer = stepper(
                        manager.memory, config, request.limits.model_dump(mode="json")
                    )
                run = await manager.submit(request)
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
            elif args.command == "explain":
                from .assembler import Assembler
                from .explain import render

                assembler = Assembler(memory, config)
                run = await memory.run(args.run_id)
                text = render(
                    run,
                    await Telemetry(memory, config).metrics(args.run_id),
                    await memory.all_events(args.run_id),
                    lambda ref: memory.artifacts.read_json(ref) if ref else None,
                    (run.get("request") or {}).get("limits") or {},
                    reconstruct=assembler.reconstruct,
                )
                if args.output:
                    Path(args.output).write_text(text + "\n")
                else:
                    print(text)
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
    runner.add_argument("--adapter", choices=["coding", "ledger", "research"], default="coding")
    runner.add_argument("--method", choices=["react", "econocontext"], default="econocontext")
    runner.add_argument(
        "--task", help="JSON Task file, including optional pinned local repository or corpus"
    )
    runner.add_argument("--max-cost", type=float, help="Stop the run above this known spend")
    runner.add_argument("--max-attempts", type=int, help="Model attempts allowed across the run")
    runner.add_argument("--deadline", type=float, help="Run deadline in seconds")
    runner.add_argument("--output-tokens", type=int, help="Output token limit per model call")
    runner.add_argument("--context-tokens", type=int, help="Context budget per assembled request")
    runner.add_argument("--max-children", type=int, help="Reusable child workers allowed")
    runner.add_argument("--retries", type=int, help="Retries per model call on transient errors")
    runner.add_argument(
        "--step", action="store_true", help="Narrate each component handoff and pause between them"
    )
    sub.add_parser("demo")
    profiles = sub.add_parser("profiles")
    profiles.add_argument("run_ids", nargs="+")
    profiles.add_argument("--output", required=True)
    export = sub.add_parser("export")
    export.add_argument("run_id")
    export.add_argument("--output")
    explain = sub.add_parser("explain")
    explain.add_argument("run_id")
    explain.add_argument("--output")
    reconstruct = sub.add_parser("reconstruct")
    reconstruct.add_argument("manifest")
    reconstruct.add_argument("--output")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
