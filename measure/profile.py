"""Context profile of runs, per task: how big the prompts and the tool outputs get.

Why it exists: EconoContext's thresholds were set for Gemini (compaction from 30k-token prompts; a
tool output is "big" from 1,000 tokens). Before anything is chosen for Qwen3.8 on Purdue, this shows
what the agent's context actually looks like on each task:
- pass, calls;
- the peak prompt P and the call where it occurred;
- ΣP, ΣU, ΣG and PFLOPs, as measure/report.py computes them (rebuilt, validated prompts);
- the largest single tool output, and how many tool outputs reach 1,000 tokens.
Tool outputs are counted with the model's own tokenizer as the model saw them (after the agent's
30,000-character cap), once each (by tool_call_id) across the run's logged requests.

Run: .venv/bin/python -m measure.profile runs/tp1 [--out runs/tp1]
"""

import argparse
import csv
import json
from pathlib import Path

from .flops import measure_log
from .models import MEASURED, load
from .report import runs

BIG = 1_000
COLUMNS = ["task", "arm", "repeat", "pass", "calls", "peak_P", "peak_call", "P", "U", "G", "PFLOPs", "largest_tool_output",
           "tool_outputs", "tool_outputs_ge_1000", "valid", "run_id"]


def tool_outputs(calls_log: Path, tokenizer) -> list[int]:
    """Token counts of every tool output the model was shown, once each."""
    seen = {}
    for line in calls_log.read_text().splitlines():
        row = json.loads(line)
        for m in (row.get("request") or {}).get("messages") or []:
            if m.get("role") == "tool" and m.get("tool_call_id") not in seen and isinstance(m.get("content"), str):
                seen[m["tool_call_id"]] = len(tokenizer.encode(m["content"], add_special_tokens=False))
    return list(seen.values())


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("paths", nargs="+")
    p.add_argument("--out", help="folder for profile.csv (default: the first path)")
    a = p.parse_args()
    from transformers import AutoTokenizer
    model = load(MEASURED)
    tokenizer = AutoTokenizer.from_pretrained(MEASURED, revision=model["revision"])
    table = []
    for run in runs(a.paths):
        result = measure_log(run["calls_log"], tokenizer, model["C_token"], model["C_attn"])
        rows, t = result["rows"], result["totals"]
        peak = max(rows, key=lambda r: r["P"]) if rows else {"P": 0, "call_no": None}
        outputs = tool_outputs(run["calls_log"], tokenizer)
        parts = run["summary"]["run_id"].split(".")
        table.append({"task": parts[2] if len(parts) == 4 else run["summary"]["run_id"],
                      "arm": parts[1] if len(parts) == 4 else "", "repeat": parts[3] if len(parts) == 4 else "",
                      "pass": run["summary"].get("reward"),
                      "calls": t["calls"], "peak_P": peak["P"], "peak_call": peak["call_no"], "P": t["P"], "U": t["U"],
                      "G": t["G"], "PFLOPs": round(t["F"] / 1e15, 4), "largest_tool_output": max(outputs, default=0),
                      "tool_outputs": len(outputs), "tool_outputs_ge_1000": sum(n >= BIG for n in outputs),
                      "valid": f"{t['valid']}/{t['valid'] + t['invalid']}", "run_id": run["summary"]["run_id"]})
    print(f"{'task':40s} {'arm':10s} {'rep':>3s} {'pass':>4s} {'calls':>5s} {'peak P':>7s} {'@call':>5s} {'ΣP':>9s} {'ΣU':>8s} {'ΣG':>7s} "
          f"{'PFLOPs':>7s} {'max out':>7s} {'outs':>4s} {'>=1k':>4s} {'valid':>6s}")
    for r in table:
        print(f"{r['task'][:40]:40s} {r['arm']:10s} {r['repeat']:>3s} {str(r['pass']):>4s} {r['calls']:5d} {r['peak_P']:7,d} {str(r['peak_call']):>5s} "
              f"{r['P']:9,d} {r['U']:8,d} {r['G']:7,d} {r['PFLOPs']:7.3f} {r['largest_tool_output']:7,d} "
              f"{r['tool_outputs']:4d} {r['tool_outputs_ge_1000']:4d} {r['valid']:>6s}")
    out = Path(a.out or a.paths[0])
    out.mkdir(parents=True, exist_ok=True)
    with (out / "profile.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(table)
    print(f"wrote {out / 'profile.csv'}")


if __name__ == "__main__":
    main()
