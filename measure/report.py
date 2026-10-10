"""Per-run cost table: prefix-reuse FLOPs of each run, from the gateway's call log.

Why it exists: the one report every experiment uses. It is given run folders, or label folders that
hold them (each with the runner's summary.json), or a calls.jsonl directly. For each run it prints
one row:
- task, arm, pass, calls (agent and summary), retries;
- ΣP, ΣR, ΣU, ΣG, hit share ΣR/ΣP;
- PFLOPs: linear, attention, total;
- the summary calls' PFLOPs, the CLM-convention variant, and the total on Purdue's actual ids;
- Jev input tokens and fallbacks;
- how many calls passed validation.

It writes report.csv (one row per run) and calls.csv (one row per call). A call whose rebuilt prompt
fails validation is listed, and the exit code is 2: review those runs before using their numbers.
The tokenizer is the measured model's own, at the revision in measure/model_constants.json.

Run: .venv/bin/python -m measure.report runs/ta1 [runs/gateway/<run_id>/calls.jsonl ...] [--out runs/ta1]
"""

import argparse
import csv
import json
import sys
from pathlib import Path

from .flops import measure_log
from .models import MEASURED, load

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = ["run_id", "task", "arm", "pass", "calls", "summary_calls", "retries", "P", "R", "U", "G", "hit_share",
           "PFLOPs_lin", "PFLOPs_attn", "PFLOPs", "PFLOPs_summary", "PFLOPs_clm_aux", "PFLOPs_server",
           "jev_input_tokens", "jev_fallbacks", "valid", "invalid"]


def runs(paths: list[str]) -> list[dict]:
    """Each run: its calls.jsonl and, when the runner made it, its summary.json."""
    found = []
    for p in map(Path, paths):
        if p.name == "calls.jsonl":
            found.append({"calls_log": p, "summary": {"run_id": p.parent.name}})
            continue
        for summary_path in sorted(p.rglob("summary.json")) if p.is_dir() else []:
            summary = json.loads(summary_path.read_text())
            if summary.get("skipped") or not summary.get("calls_log"):
                continue
            found.append({"calls_log": ROOT / summary["calls_log"], "summary": summary, "dir": summary_path.parent})
    return found


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("paths", nargs="+")
    p.add_argument("--out", help="folder for report.csv and calls.csv (default: the first path's folder)")
    a = p.parse_args()
    from transformers import AutoTokenizer
    model = load(MEASURED)
    tokenizer = AutoTokenizer.from_pretrained(MEASURED, revision=model["revision"])
    out = Path(a.out or (a.paths[0] if Path(a.paths[0]).is_dir() else Path(a.paths[0]).parent))
    table, per_call, invalid = [], [], []
    for run in runs(a.paths):
        result = measure_log(run["calls_log"], tokenizer, model["C_token"], model["C_attn"])
        s, t = run["summary"], result["totals"]
        run_id = s["run_id"]
        parts = run_id.split(".")
        owner = s.get("owner") or {}
        table.append({"run_id": run_id, "task": parts[2] if len(parts) == 4 else "", "arm": parts[1] if len(parts) == 4 else "",
                      "pass": s.get("reward"), "calls": t["calls"], "summary_calls": t["summary_calls"],
                      "retries": t["retries"], "P": t["P"], "R": t["R"], "U": t["U"], "G": t["G"],
                      "hit_share": round(t["hit_share"], 4), "PFLOPs_lin": t["F_lin"] / 1e15,
                      "PFLOPs_attn": t["F_attn"] / 1e15, "PFLOPs": t["F"] / 1e15, "PFLOPs_summary": t["F_summary"] / 1e15,
                      "PFLOPs_clm_aux": t["F_clm_aux"] / 1e15,
                      "PFLOPs_server": t["F_server"] / 1e15 if t["F_server"] is not None else None,
                      "jev_input_tokens": (owner.get("jev") or {}).get("input_tokens"),
                      "jev_fallbacks": (s.get("jev_fallbacks") or {}).get("fell_back"),
                      "valid": t["valid"], "invalid": t["invalid"]})
        checks = {c["call_no"]: c for c in result["validation"]}
        for row in result["rows"]:
            per_call.append({"run_id": run_id, **row, "validation": checks[row["call_no"]]["note"]})
        invalid += [(run_id, c) for c in result["validation"] if not c["ok"]]
    print(f"{'run':44s} {'pass':>4s} {'calls':>7s} {'retry':>5s} {'ΣP':>9s} {'ΣR':>9s} {'ΣG':>7s} {'hit':>5s} "
          f"{'PF lin':>7s} {'PF attn':>7s} {'PFLOPs':>7s} {'PF summ':>7s} {'PF CLM':>7s} {'PF srv':>7s} {'Jev':>7s} {'fb':>3s} {'ok':>5s}")
    for r in table:
        server = f"{r['PFLOPs_server']:7.3f}" if r["PFLOPs_server"] is not None else f"{'-':>7s}"
        print(f"{r['run_id'][:44]:44s} {str(r['pass']):>4s} {r['calls']:4d}/{r['summary_calls']:<2d} {r['retries']:5d} "
              f"{r['P']:9,d} {r['R']:9,d} {r['G']:7,d} {r['hit_share']:5.3f} {r['PFLOPs_lin']:7.3f} {r['PFLOPs_attn']:7.3f} "
              f"{r['PFLOPs']:7.3f} {r['PFLOPs_summary']:7.3f} {r['PFLOPs_clm_aux']:7.3f} {server} "
              f"{str(r['jev_input_tokens'] or '-'):>7s} {str(r['jev_fallbacks'] if r['jev_fallbacks'] is not None else '-'):>3s} "
              f"{r['valid']:2d}/{r['valid'] + r['invalid']:<2d}")
    out.mkdir(parents=True, exist_ok=True)
    with (out / "report.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(table)
    if per_call:
        with (out / "calls.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(per_call[0]))
            w.writeheader()
            w.writerows(per_call)
    print(f"wrote {out / 'report.csv'} and {out / 'calls.csv'}")
    for run_id, check in invalid:
        print(f"INVALID {run_id} call {check['call_no']}: {check['note']}")
    sys.exit(2 if invalid else 0)


if __name__ == "__main__":
    main()
