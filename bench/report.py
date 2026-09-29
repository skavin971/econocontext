"""Human and machine-readable cost/timing reports for one run label.

Examples::

    .venv/bin/python bench/report.py --label check1
    .venv/bin/python bench/report.py --label check1 --format json --output report.json
    .venv/bin/python bench/report.py --label check1 --format csv --output report.csv

Exports contain numeric usage, cost, timing and decision data only. Full prompts,
tool output and raw provider payloads remain in the SQLite audit database.
"""

import argparse
import csv
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from econocontext import config as config_module  # noqa: E402
from econocontext.pricing.ledger import summary  # noqa: E402
from econocontext.store.db import AgentDB  # noqa: E402

WATCH = ("POINTER", "RETRIEVE_FROM_STORE", "ANSWER_FROM_STORE", "REUSE_RESULT")


def fmt(x, digits=0):
    return "-" if x is None else f"{x:,.{digits}f}"


def duration_ms(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000


def pricing_metadata(cfg):
    return {
        "provider": cfg.raw["model"]["provider"],
        "model": cfg.card.model,
        "source_url": cfg.card.source_url,
        "retrieved_on": cfg.card.retrieved_on,
        "min_cacheable_tokens": cfg.card.min_cacheable_tokens,
        "periods": [
            {"valid_from": p["valid_from"], "valid_until": p["valid_until"],
             "tiers": [asdict(t) for t in p["tiers"]]}
            for p in cfg.card.periods
        ],
    }


def build_report(db: AgentDB, cfg, label: str) -> dict:
    rows = db.rows("SELECT run_id FROM runs WHERE run_id LIKE ? ORDER BY instance_id, arm, "
                   "started_at", (f"{label}:%",))
    runs = []
    for row in rows:
        run_id = row["run_id"]
        data = summary(db, run_id)
        run = data["run"]
        totals = data["totals"]
        data["run_duration_ms"] = duration_ms(run.get("started_at"), run.get("ended_at"))
        data["pricing"] = pricing_metadata(cfg)
        calls = [dict(r) for r in db.rows(
            "SELECT outcome_id, agent_id, phase, decision_id, uncached_input, cache_read, "
            "cache_write, output, reasoning, latency_ms, cost_nu, cost_usd, cost_complete, "
            "price_period, created_at FROM outcomes WHERE run_id=? ORDER BY created_at",
            (run_id,))]
        cumulative = 0.0
        for call in calls:
            cumulative += call["cost_usd"] or 0.0
            call["running_cost_usd"] = cumulative
        data["calls"] = calls
        by_phase, by_agent = {}, {}
        for call in calls:
            for grouped, key in ((by_phase, call["phase"]), (by_agent, call["agent_id"])):
                bucket = grouped.setdefault(key, {"calls": 0, "cost_nu": 0.0,
                                                   "cost_usd": 0.0, "incomplete_calls": 0})
                bucket["calls"] += 1
                bucket["cost_nu"] += call["cost_nu"] or 0.0
                bucket["cost_usd"] += call["cost_usd"] or 0.0
                bucket["incomplete_calls"] += int(not call["cost_complete"])
        data["calls_by_phase"] = by_phase
        data["calls_by_agent"] = by_agent
        data["price_periods"] = sorted({call["price_period"] for call in calls
                                         if call["price_period"]})
        data["cost"] = {
            "actual_cost_nu": totals["cost_nu"] if totals["cost_complete"] else None,
            "actual_cost_usd": totals["cost_usd"] if totals["cost_complete"] else None,
            "running_cost_nu": totals["cost_nu"] or 0.0,
            "running_cost_usd": totals["cost_usd"] or 0.0,
            "predicted_cost_nu": data["predicted_vs_actual"]["predicted_call_nu"],
            "incomplete_cost": not totals["cost_complete"],
        }
        for span in data["spans"]:
            span["metadata"] = json.loads(span["metadata"] or "{}")
        data["track"] = f"{run['arm']}{'+jev' if run.get('jev') else ''}"
        runs.append(data)

    tracks = {}
    for data in runs:
        run, totals = data["run"], data["totals"]
        track = data["track"]
        agg = tracks.setdefault(track, {"runs": 0, "resolved": 0, "calls": 0,
                                        "cost_nu": 0.0, "cost_usd": 0.0,
                                        "incomplete_calls": 0})
        agg["runs"] += 1
        agg["resolved"] += int(run.get("resolved") or 0)
        agg["calls"] += totals["calls"] or 0
        agg["cost_nu"] += totals["cost_nu"] or 0.0
        agg["cost_usd"] += totals["cost_usd"] or 0.0
        agg["incomplete_calls"] += totals["incomplete_calls"] or 0
    return {"label": label, "generated_at": datetime.now(timezone.utc).isoformat(),
            "pricing": pricing_metadata(cfg), "config_fingerprint": cfg.fingerprint,
            "runs": runs, "tracks": tracks}


def render_text(report: dict) -> str:
    lines = [f"PIPELINE CHECK '{report['label']}': a working, measured pipeline. "
             "Not an effectiveness comparison.\n"]
    for data in report["runs"]:
        run, totals, pva = data["run"], data["totals"], data["predicted_vs_actual"]
        lines.append(
            f"{run['instance_id'] or run['run_id']}  [{data['track']}/{run['mode']}]  "
            f"status={run['status']}  "
            f"resolved={'-' if run.get('resolved') is None else bool(run['resolved'])}")
        lines.append(
            f"  calls {totals['calls']} (compaction {totals['compaction_calls']})  "
            f"uncached {fmt(totals['uncached_input'])}  cache_read {fmt(totals['cache_read'])} "
            f"cache_write {fmt(totals['cache_write'])}  output {fmt(totals['output'])} "
            f"(reasoning {fmt(totals['reasoning'])})")
        if totals["cost_complete"]:
            cost = f"{fmt(totals['cost_nu'])} NU  ${totals['cost_usd'] or 0:.4f}"
        else:
            cost = f"known {fmt(totals['cost_nu'])} NU  ${totals['cost_usd'] or 0:.4f} (incomplete)"
        share = totals["cache_read_share"]
        lines.append(
            f"  cost {cost}   cache-read share "
            f"{'-' if share is None else f'{100 * share:.0f}%'}   "
            f"run duration {fmt(data['run_duration_ms'], 0)} ms   "
            f"model latency {fmt(totals['model_latency_ms'], 0)} ms")
        lines.append(
            f"  predicted vs actual (NU): {fmt(pva['predicted_call_nu'])} vs "
            f"{fmt(pva['actual_call_nu'])} over {pva['calls']} calls")
        runtime = {}
        for span in data["spans"]:
            bucket = runtime.setdefault(span["kind"], {"spans": 0, "duration_ms": 0.0})
            bucket["spans"] += 1
            bucket["duration_ms"] += span["duration_ms"] or 0.0
        if runtime:
            lines.append("  runtime " + "  ".join(
                f"{kind} {v['spans']} spans/{fmt(v['duration_ms'], 0)} ms"
                for kind, v in sorted(runtime.items())))
        applied = {}
        for decision in data["decisions"]:
            key = f"{decision['chosen']}{'*' if decision['applied'] else ''}"
            applied[key] = applied.get(key, 0) + decision["n"]
        if applied:
            lines.append(f"  decisions (chosen; * = applied): {dict(sorted(applied.items()))}")
        feasible = {k: v for k, v in data["feasible_counts"].items() if k in WATCH}
        if feasible:
            lines.append(f"  would-be (passed every gate): {feasible}")
        lines.append("")
    for track, totals in report["tracks"].items():
        lines.append(
            f"{track:9s} runs {totals['runs']}  resolved {totals['resolved']}  "
            f"calls {totals['calls']}  {fmt(totals['cost_nu'])} NU  "
            f"${totals['cost_usd']:.4f}  incomplete {totals['incomplete_calls']}")
    return "\n".join(lines) + "\n"


def csv_text(report: dict) -> str:
    import io
    fields = ["run_id", "instance_id", "track", "arm", "mode", "status", "resolved",
              "provider", "model", "config_fingerprint", "price_periods",
              "run_duration_ms", "calls", "uncached_input", "cache_read", "cache_write",
              "output", "cost_nu", "cost_usd", "actual_cost_usd", "running_cost_usd",
              "cost_complete", "incomplete_calls",
              "cache_read_share", "model_latency_ms", "predicted_call_nu", "actual_call_nu"]
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for data in report["runs"]:
        run, totals, pva = data["run"], data["totals"], data["predicted_vs_actual"]
        writer.writerow({
            "run_id": run["run_id"], "instance_id": run["instance_id"],
            "track": data["track"], "arm": run["arm"], "mode": run["mode"],
            "status": run["status"], "resolved": run.get("resolved"),
            "provider": report["pricing"]["provider"], "model": run["model"],
            "config_fingerprint": run["config_fingerprint"],
            "price_periods": ";".join(data["price_periods"]),
            "run_duration_ms": data["run_duration_ms"], **{
                key: totals.get(key) for key in fields
                if key in {"calls", "uncached_input", "cache_read", "cache_write", "output",
                           "cost_nu", "cost_usd", "cost_complete", "incomplete_calls",
                           "cache_read_share", "model_latency_ms"}},
            "actual_cost_usd": data["cost"]["actual_cost_usd"],
            "running_cost_usd": data["cost"]["running_cost_usd"],
            "predicted_call_nu": pva["predicted_call_nu"],
            "actual_call_nu": pva["actual_call_nu"],
        })
    return stream.getvalue()


def main(label: str, output_format: str = "text", output: str | None = None) -> None:
    cfg = config_module.load(ROOT / "config")
    db = AgentDB(ROOT / cfg.raw["storage"]["db_path"])
    report = build_report(db, cfg, label)
    if output_format == "text":
        rendered = render_text(report)
    elif output_format == "json":
        rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    else:
        rendered = csv_text(report)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--format", choices=("text", "json", "csv"), default="text")
    parser.add_argument("--output")
    args = parser.parse_args()
    main(args.label, args.format, args.output)
