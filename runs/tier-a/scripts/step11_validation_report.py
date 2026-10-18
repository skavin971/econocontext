"""Step 11 validation: per run, next to clm-32k at the same settings - share of calls at the 2,048 cap,
cuts right after a nudge, steps, pass and end reason, PFLOPs and validation. Reads runs/<label>/report.csv
(measure.report) and the gateway logs."""
import csv
import json
import sys

NUDGES = ("Continue by calling a tool", "(No tool call in your last turn", "Your last reply made no tool call",
          "Your last reply was cut off")
measured = {}
for label in ("tc11v", "tc10s"):
    for r in csv.DictReader(open(f"runs/{label}/report.csv")):
        measured[r["run_id"]] = r
RUNS = [("tc11v.base-32k.bandit-delayed-feedback.r1", "runs/tc11v/base-32k/bandit-delayed-feedback-r1"),
        ("tc11v.summary-32k.bandit-delayed-feedback.r1", "runs/tc11v/summary-32k/bandit-delayed-feedback-r1"),
        ("tc10s.clm-32k.bandit-delayed-feedback.r1", "runs/tc10s/clm-32k/bandit-delayed-feedback-r1"),
        ("tc11v.base-32k.scan-linux-persistence-artifacts.r1", "runs/tc11v/base-32k/scan-linux-persistence-artifacts-r1"),
        ("tc11v.clm-32k.scan-linux-persistence-artifacts.r1", "runs/tc11v/clm-32k/scan-linux-persistence-artifacts-r1"),
        ("tc10s.raw-32k.bandit-delayed-feedback.r1", "runs/tc10s/raw-32k/bandit-delayed-feedback-r1"),
        ("tc10s.summary-32k.bandit-delayed-feedback.r1", "runs/tc10s/summary-32k/bandit-delayed-feedback-r1")]
print("| run | interface | pass | end | agent calls (steps) | summary calls | at the 2,048 cap | cut right after a nudge | PFLOPs | validated |")
print("|---|---|---|---|---|---|---|---|---|---|")
for run_id, folder in RUNS:
    try:
        s = json.load(open(f"{folder}/summary.json"))
        rows = [json.loads(l) for l in open(f"runs/gateway/{run_id}/calls.jsonl")]
    except FileNotFoundError:
        print(f"| {run_id} | (missing) |"); continue
    agent = [r for r in rows if r.get("status") == 200 and r.get("kind") == "agent"]
    cap = [r for r in agent if r["usage"]["completion_tokens"] >= r["request"]["max_tokens"]]
    after_nudge = [r for r in agent if r["request"]["messages"][-1]["role"] == "user"
                   and str(r["request"]["messages"][-1].get("content") or "").startswith(NUDGES)]
    cut_after = [r for r in after_nudge if r in cap]
    m = measured.get(run_id, {})
    interface = s.get("interface") or ("clm" if "clm" in run_id else "legacy")
    print(f"| {run_id} | {interface} | {'pass' if s.get('reward') == 1.0 else 'fail' if s.get('reward') is not None else 'NG'} | "
          f"{s.get('end') or ('CLM finished' if 'clm' in run_id else '-')} | {len(agent)} | {s.get('calls_by_kind', {}).get('summary', 0)} | "
          f"{len(cap)} ({len(cap) / max(1, len(agent)):.0%}) | {len(cut_after)} of {len(after_nudge)} nudges | "
          f"{float(m.get('PFLOPs', 'nan')):.3f} | {m.get('valid')}/{int(m.get('valid', 0)) + int(m.get('invalid', 0))} |")
