import argparse
import asyncio
import json
from pathlib import Path

from ..config import Config
from ..contracts import RunRequest, Task
from ..runtime.manager import Manager
from ..runtime.telemetry import Telemetry, build_profiles
from ..store.memory import MemoryStore


def output(data, path=None):
    text = json.dumps(data, indent=2)
    if path:
        Path(path).write_text(text + "\n")
    else:
        print(text)


def stepper(memory, config, limits):
    """Narrate each event as it is written, pausing so one run can be walked."""
    from ..assembler import Assembler
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


LIMIT_FIELDS = (
    "max_cost",
    "max_attempts",
    "deadline",
    "output_tokens",
    "context_tokens",
    "max_children",
    "retries",
    "plan_pressure",
    "observation_tokens",
)


def build_task(args):
    if args.task:
        return Task.model_validate_json(Path(args.task).read_text())
    return Task(
        adapter=args.adapter,
        prompt=args.prompt,
        data=args.data,
        commit=args.commit,
        fixture=args.fixture,
    )


def build_request(args, task, method):
    request = RunRequest(task=task, method=method)
    overrides = {
        field: getattr(args, field, None)
        for field in LIMIT_FIELDS
        if getattr(args, field, None) is not None
    }
    if overrides:
        request = request.model_copy(
            update=dict(limits=request.limits.model_copy(update=overrides))
        )
    return request


def money(value):
    return f"${value:.4f}"


def summarize(arms):
    """Cost and time for each arm, side by side, in the order they were run."""
    header = f"{'ARM':<14}{'STATUS':<13}{'VERIFIED':<10}{'COST':>11}{'WALL':>10}{'CALLS':>7}{'TOKENS IN':>12}{'OUT':>9}"
    lines = [header, "-" * len(header)]
    for name, metrics in arms.items():
        lines.append(
            f"{name:<14}{metrics['status']:<13}{metrics['verification']:<10}"
            f"{money(metrics['known_cost']):>11}"
            f"{metrics['wall_seconds']:>9.1f}s"
            f"{metrics['model_attempts']:>7}"
            f"{metrics['tokens']['uncached'] + metrics['tokens']['cached']:>12,}"
            f"{metrics['tokens']['output']:>9,}"
        )
    if len(arms) == 2:
        (a, first), (b, second) = arms.items()
        dc = second["known_cost"] - first["known_cost"]
        dt = second["wall_seconds"] - first["wall_seconds"]
        share = (dc / first["known_cost"] * 100) if first["known_cost"] else 0
        lines += [
            "-" * len(header),
            f"{b} vs {a}:  cost {dc:+.4f} ({share:+.1f}%)   wall {dt:+.1f}s",
        ]
    incomplete = [n for n, m in arms.items() if not m["cost_complete"]]
    if incomplete:
        lines.append(f"NOTE  cost incomplete for {', '.join(incomplete)} — some calls had no usage")
    return "\n".join(lines)


async def compare(args, config):
    """Run every method over one identical task and report cost against time."""
    from agents import build

    from ..assembler import Assembler
    from .explain import render

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    task = build_task(args)
    arms, manager = {}, await Manager(config, adapter=build).start()
    try:
        for method in args.methods:
            print(f"\n=== {method} ===", flush=True)
            run = await manager.submit(build_request(args, task, method))
            final = await manager.wait(run["id"])
            metrics = await manager.telemetry.metrics(run["id"])
            arms[method] = metrics
            print(
                f"{final['status']}  {money(metrics['known_cost'])}  "
                f"{metrics['wall_seconds']:.1f}s  {metrics['model_attempts']} calls",
                flush=True,
            )
            events = await manager.memory.all_events(run["id"])
            (out / f"{method}-walkthrough.txt").write_text(
                render(
                    final,
                    metrics,
                    events,
                    lambda ref: manager.memory.artifacts.read_json(ref) if ref else None,
                    (final.get("request") or {}).get("limits") or {},
                    reconstruct=Assembler(manager.memory, config).reconstruct,
                )
                + "\n"
            )
            output(dict(run=final, metrics=metrics, trace=events), out / f"{method}-trace.json")
    finally:
        await manager.close()
    table = summarize(arms)
    print("\n" + table)
    (out / "summary.txt").write_text(table + "\n")
    output({m: arms[m] for m in arms}, out / "summary.json")
    print(f"\nwritten to {out}/")
    if config.call_log:
        print(f"per-call logs in {Path(config.call_log)}/")


async def show_schema(memory, args):
    """The DDL in force, and what a real database actually accumulated under it."""
    from ..store.memory import SCHEMA

    if not args.counts_only:
        print(SCHEMA.rstrip())
        print()
    tables = [
        r["name"]
        for r in await memory.query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]
    scope = " WHERE run_id=?" if args.run_id else ""
    params = (args.run_id,) if args.run_id else ()
    print(f"{'TABLE':<16}{'ROWS':>10}{'PAYLOAD BYTES':>16}   {'per row':>9}")
    print("-" * 55)
    for table in tables:
        columns = {
            r["name"] for r in await memory.query(f"SELECT name FROM pragma_table_info('{table}')")
        }
        where = scope if "run_id" in columns else ""
        args_ = params if where else ()
        rows = (await memory.query(f"SELECT count(*) AS n FROM {table}{where}", args_))[0]["n"]
        size = 0
        if "payload" in columns and rows:
            size = (
                await memory.query(f"SELECT sum(length(payload)) AS b FROM {table}{where}", args_)
            )[0]["b"] or 0
        per = f"{size / rows:,.0f}" if rows and size else "-"
        print(f"{table:<16}{rows:>10,}{size:>16,}   {per:>9}")
    blobs = list(memory.artifacts.root.rglob("*")) if memory.artifacts.root.exists() else []
    files_ = [b for b in blobs if b.is_file()]
    print(
        f"\nartifact store   {len(files_):,} objects"
        f"   {sum(f.stat().st_size for f in files_):,} bytes on disk"
    )
    print("Rows hold references; the bytes they point at live in the artifact store.")


async def run(args):
    config = Config.from_env()
    if args.data_dir:
        config.data_dir = Path(args.data_dir)
    if args.command == "compare":
        await compare(args, config)
    elif args.command == "run":
        from agents import build

        manager = await Manager(config, adapter=build).start()
        try:
            request = build_request(args, build_task(args), args.method)
            if args.step:
                manager.memory.observer = stepper(
                    manager.memory, config, request.limits.model_dump(mode="json")
                )
            run = await manager.submit(request)
            final = await manager.wait(run["id"])
            reports = [dict(run=final, metrics=await manager.telemetry.metrics(run["id"]))]
            output(reports)
            if any(r["run"]["status"] != "succeeded" for r in reports):
                raise SystemExit(1)
        finally:
            await manager.close()
    else:
        memory = await MemoryStore(config.data_dir).open()
        try:
            if args.command == "schema":
                await show_schema(memory, args)
            elif args.command == "profiles":
                output(await build_profiles(memory, args.run_ids), args.output)
            elif args.command == "explain":
                from ..assembler import Assembler
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
                from ..assembler import Assembler

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


def add_task_flags(p):
    """The task and limit flags shared by `run` and `compare`."""
    p.add_argument(
        "--adapter", choices=["coding", "research", "swe"], default="coding", help="Domain toolset"
    )
    p.add_argument("--prompt", help="What the harness should do")
    p.add_argument("--data", help="Directory of your own files, or a git repo")
    p.add_argument("--commit", help="Pin a commit when --data is a git repo")
    p.add_argument(
        "--fixture",
        help="Built-in demonstration task instead of your own data",
    )
    p.add_argument(
        "--task", help="JSON Task file, including optional pinned local repository or corpus"
    )
    p.add_argument("--max-cost", type=float, help="Stop the run above this known spend")
    p.add_argument("--max-attempts", type=int, help="Model attempts allowed across the run")
    p.add_argument("--deadline", type=float, help="Run deadline in seconds")
    p.add_argument("--output-tokens", type=int, help="Output token limit per model call")
    p.add_argument("--context-tokens", type=int, help="Context budget per assembled request")
    p.add_argument("--max-children", type=int, help="Reusable child workers allowed")
    p.add_argument("--retries", type=int, help="Retries per model call on transient errors")
    p.add_argument(
        "--plan-pressure", type=float, help="Context fill fraction at which delegation is offered"
    )
    p.add_argument("--observation-tokens", type=int, help="Smallest observation worth an operation")
    return p


def main():
    parser = argparse.ArgumentParser(prog="econocontext")
    parser.add_argument("--data-dir")
    sub = parser.add_subparsers(dest="command", required=True)
    runner = add_task_flags(sub.add_parser("run"))
    runner.add_argument("--method", choices=["react", "econocontext"], default="econocontext")

    versus = add_task_flags(
        sub.add_parser("compare", help="Run each method over one task; report cost against time")
    )
    versus.add_argument(
        "--methods",
        nargs="+",
        choices=["react", "econocontext"],
        default=["react", "econocontext"],
        help="Methods to run, in order. Every other setting is held identical.",
    )
    versus.add_argument(
        "--output", default="docs/runs/latest", help="Directory for trajectories and the summary"
    )

    schema = sub.add_parser("schema", help="The store's DDL, and what it has accumulated")
    schema.add_argument("run_id", nargs="?", help="Count one run only, instead of the whole store")
    schema.add_argument(
        "--counts-only", action="store_true", help="Skip the DDL and print only the table sizes"
    )
    runner.add_argument(
        "--step", action="store_true", help="Narrate each component handoff and pause between them"
    )
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
