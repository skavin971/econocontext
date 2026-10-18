"""Compute spent on solved vs failed runs, per arm (2026-10-18). Offline; reads existing results only.

TBLite: every logged run (tc9, tc10s, tc11v): reward from summary.json, canonical PFLOPs from step 14b's runs.csv
(Eq. 9 on the canonical rebuild, summary calls included), the run's end (submitted, or not: turn limit, budget,
overflow or crash). BrowseComp-Plus pilot: runs/tier-a/step13a/pilot.jsonl (judged correctness, canonical PFLOPs, end).
Per arm: runs, solved, PFLOPs per run, mean PFLOPs of solved and of failed runs, the share of the arm's FLOPs spent on
failed runs, and PFLOPs per solved task. Only numbers and ids are printed.

Use: python3 runs/tier-a/scripts/failure_cost.py
"""

import csv
import glob
import json
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
F = {r["run_id"]: float(r["F"]) / 1e15 for r in csv.DictReader((ROOT / "runs/tier-a/step14b/runs.csv").open())}


def table(title, rows):
    print(f"\n{title}")
    print(f"{'arm':12} {'runs':>4} {'solved':>6} {'PF/run':>7} {'PF solved':>9} {'PF failed':>9} "
          f"{'FLOPs on failed':>15} {'PF per solve':>12}  failed runs by end")
    by = defaultdict(list)
    for r in rows:
        by[r["arm"]].append(r)
    for arm, rs in sorted(by.items()):
        ok = [r for r in rs if r["solved"]]
        bad = [r for r in rs if not r["solved"]]
        tot = sum(r["PF"] for r in rs)
        mean = lambda xs: statistics.mean(xs) if xs else float("nan")   # noqa: E731
        ends = defaultdict(int)
        for r in bad:
            ends[r["end"]] += 1
        print(f"{arm:12} {len(rs):4d} {len(ok):6d} {tot / len(rs):7.2f} {mean([r['PF'] for r in ok]):9.2f} "
              f"{mean([r['PF'] for r in bad]):9.2f} {100 * sum(r['PF'] for r in bad) / tot:14.1f}% "
              f"{(tot / len(ok)) if ok else float('inf'):12.2f}  {dict(ends)}")


tblite = []
for path in glob.glob(str(ROOT / "runs/tc*/**/summary.json"), recursive=True):
    if "envfail" in path or "/tc9s/" in path:
        continue
    s = json.loads(Path(path).read_text())
    if s.get("run_id") not in F:
        continue
    log = ROOT / (s.get("calls_log") or "")
    submitted = False
    if log.exists():
        for line in log.read_text().splitlines():
            row = json.loads(line)
            msg = ((row.get("response") or {}).get("choices") or [{}])[0].get("message") or {} if isinstance(row.get("response"), dict) else {}
            for c in msg.get("tool_calls") or []:
                fn = c.get("function") or {}
                if fn.get("name") == "submit" or "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in (fn.get("arguments") or ""):
                    submitted = True
    end = s.get("end") or ("crash" if s.get("exception") and not s.get("overflow") else "overflow" if s.get("overflow")
                          else "submitted" if submitted else "turn limit / no submit")
    tblite.append({"arm": s["arm"], "run_id": s["run_id"], "solved": s.get("reward") == 1.0, "PF": F[s["run_id"]], "end": end})
table("TBLite, all logged runs (canonical PFLOPs)", tblite)

bcp = []
for line in (ROOT / "runs/tier-a/step13a/pilot.jsonl").read_text().splitlines():
    d = json.loads(line)
    bcp.append({"arm": d["arm"], "run_id": d["run_id"], "solved": bool(d["judged"]), "PF": d["PF"],
                "end": f"{d['end']}{'' if d['answer_file'] else ' (no answer)'}", "q": d["query_id"]})
table("BrowseComp-Plus pilot (canonical PFLOPs; judged correct)", bcp)
shared = sorted({r["q"] for r in bcp if r["arm"] == "base"} & {r["q"] for r in bcp if r["arm"] == "clm"})
print(f"\nBrowseComp-Plus, the {len(shared)} questions both arms ran:")
for q in shared:
    b = next(r for r in bcp if r["arm"] == "base" and r["q"] == q)
    c = next(r for r in bcp if r["arm"] == "clm" and r["q"] == q)
    print(f"  q{q}: base {b['PF']:.2f} PF {'solved' if b['solved'] else 'failed'} ({b['end']}); "
          f"CLM {c['PF']:.2f} PF {'solved' if c['solved'] else 'failed'} ({c['end']}); CLM/base {c['PF'] / b['PF']:.1f}x")
print("\nThe paper (Qwen3.6-27B, 830 questions, 23,560-token budget): CLM 60.2% at 10.98 PFLOPs per question "
      f"(repository figure scr_bcp830_strip.png) -> {10.98 / 0.602:.1f} PFLOPs per correct answer if the average is over all "
      "questions (the paper does not say).")
