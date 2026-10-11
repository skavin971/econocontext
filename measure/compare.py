"""Compare arms run on the same tasks and repeats: the tables behind a results write-up.

Why it exists: step 9 asks one question of the arms (raw, econo+jev, clm-32k, clm-full): quality
(pass rate, overflows) and compute (prefix-reuse FLOPs) in real settings. This builds the tables from
what measure/report.py and measure/profile.py wrote (report.csv, arms.csv, profile.csv), the runs'
summary.json files (so trials that made no call still count as not graded), and the raw calls.jsonl
(to re-derive the totals independently):
  - per task x arm: passes out of the repeats, mean PFLOPs (range), mean calls, ΣU, ΣG, overflows;
  - per arm: passes, total PFLOPs, PFLOPs on the cells (task, repeat) every arm passed, and the FLOPs
    split into linear prefill (C_token·ΣU), linear output (C_token·ΣG) and attention;
  - the long-context tasks on their own; what EconoContext and CLM did, per task;
  - paired comparisons by task and repeat against raw, with exact two-sided sign tests;
  - checks: every call validated; ΣG and ΣP re-derived from the server's own usage.
An overflow (a request refused over the model's window) is a FAIL; a trial that was never graded
(e.g. its environment did not start) is counted apart. No claims beyond the numbers.

Run: .venv/bin/python -m measure.compare runs/tc9 [--out docs/results/<date>-tblite-arms/tables.md]
"""

import argparse
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from .models import MEASURED, load

ROOT = Path(__file__).resolve().parents[1]
ARMS = ["raw", "econo+jev", "clm-32k", "clm-full"]
LONG = ["scan-linux-persistence-artifacts", "bandit-delayed-feedback", "malicious-package-forensics"]


def sign_test(wins: int, losses: int) -> float:
    """Exact two-sided sign test (ties dropped)."""
    n, k = wins + losses, min(wins, losses)
    return 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def load_runs(label_dir: Path) -> dict:
    """(task, arm, repeat) -> one record with the outcome and, if it made calls, the measured totals."""
    measured = {r["run_id"]: r for r in csv.DictReader(open(label_dir / "report.csv"))}
    profile = {r["run_id"]: r for r in csv.DictReader(open(label_dir / "profile.csv"))}
    runs = {}
    for path in sorted(label_dir.glob("*/*/summary.json")):
        s = json.loads(path.read_text())
        m = measured.get(s["run_id"])
        outcome = ("OVF" if s.get("overflow") else "pass" if s.get("reward") == 1.0 else
                   "fail" if s.get("reward") is not None else "NG")
        rec = {"task": s["task"], "arm": s["arm"], "repeat": int(s["repeat"]), "outcome": outcome,
               "exception": s.get("exception"), "calls_log": s.get("calls_log"), "measured": m is not None}
        if m:
            rec.update({k: float(m[k]) for k in ("PFLOPs", "PFLOPs_lin", "PFLOPs_attn")},
                       **{k: int(m[k]) for k in ("calls", "P", "U", "G", "valid", "invalid", "retries", "repeats")})
            rec["peak_P"] = int(profile[s["run_id"]]["peak_P"]) if s["run_id"] in profile else None
        runs[(s["task"], s["arm"], int(s["repeat"]))] = rec
    return runs


def fmt(x: float, nd: int = 3) -> str:
    return f"{x:.{nd}f}"


def tables(label_dir: Path) -> str:
    runs = load_runs(label_dir)
    model = load(MEASURED)
    actions = {(r["task"], r["arm"]): r for r in csv.DictReader(open(label_dir / "arms.csv"))}
    tasks = sorted({k[0] for k in runs}, key=lambda t: (t in LONG, t))
    repeats = sorted({k[2] for k in runs})
    out = []

    def passes(recs):
        return sum(r["outcome"] == "pass" for r in recs), sum(r["outcome"] != "NG" for r in recs)

    # 2. per task x arm
    out += ["## Per task and arm", "",
            "Passes out of the repeats (an overflow is a FAIL; NG = not graded), mean PFLOPs per trial (min-max), mean "
            "calls, ΣU (uncached prompt tokens), ΣG (output tokens, thinking included) and overflows. Means are over the "
            "trials that made calls, whatever their outcome.", "",
            "| task | arm | passes | PFLOPs mean (range) | calls | ΣU | ΣG | overflows |", "|---|---|---|---|---|---|---|---|"]
    for t in tasks:
        for arm in ARMS:
            recs = [runs[(t, arm, r)] for r in repeats if (t, arm, r) in runs]
            m = [r for r in recs if r["measured"]]
            p, graded = passes(recs)
            ng = len(recs) - graded
            pf = [r["PFLOPs"] for r in m]
            out.append(f"| {t} | {arm} | {p}/{len(recs)}{f' ({ng} NG)' if ng else ''} | "
                       + (f"{fmt(statistics.mean(pf))} ({fmt(min(pf))}-{fmt(max(pf))})" if pf else "-") + " | "
                       + (f"{statistics.mean(r['calls'] for r in m):.1f} | {statistics.mean(r['U'] for r in m):,.0f} | "
                          f"{statistics.mean(r['G'] for r in m):,.0f}" if m else "- | - | -")
                       + f" | {sum(r['outcome'] == 'OVF' for r in recs)} |")
    # 3. per arm totals
    all_pass = [(t, r) for t in tasks for r in repeats if all(runs.get((t, a, r), {}).get("outcome") == "pass" for a in ARMS)]
    out += ["", "## Per arm", "",
            f"Total PFLOPs over every trial that made calls, split into linear prefill (C_token·ΣU), linear output "
            f"(C_token·ΣG) and attention; and PFLOPs on the {len(all_pass)} cells (task, repeat) where all four arms passed "
            f"(like-for-like work). C_token = {model['C_token']:,}.", "",
            "| arm | passes / 30 | graded | overflows | total PFLOPs | linear prefill | linear output | attention | "
            f"PFLOPs on the {len(all_pass)} all-pass cells | calls |", "|---|---|---|---|---|---|---|---|---|---|"]
    for arm in ARMS:
        recs = [r for k, r in runs.items() if k[1] == arm]
        m = [r for r in recs if r["measured"]]
        p, graded = passes(recs)
        U, G = sum(r["U"] for r in m), sum(r["G"] for r in m)
        common = sum(runs[(t, arm, r)]["PFLOPs"] for t, r in all_pass)
        out.append(f"| {arm} | {p} | {graded} | {sum(r['outcome'] == 'OVF' for r in recs)} | {fmt(sum(r['PFLOPs'] for r in m), 2)} | "
                   f"{fmt(model['C_token'] * U / 1e15, 2)} | {fmt(model['C_token'] * G / 1e15, 2)} | "
                   f"{fmt(sum(r['PFLOPs_attn'] for r in m), 2)} | {fmt(common, 2)} | {sum(r['calls'] for r in m)} |")
    # 4. the long-context tasks
    out += ["", "## The three long-context tasks", "",
            "| task | arm | passes | overflows | mean peak P | PFLOPs mean | PFLOPs by repeat |", "|---|---|---|---|---|---|---|"]
    for t in LONG:
        for arm in ARMS:
            recs = [runs[(t, arm, r)] for r in repeats if (t, arm, r) in runs]
            m = [r for r in recs if r["measured"]]
            p, graded = passes(recs)
            peaks = [r["peak_P"] for r in m if r.get("peak_P")]
            out.append(f"| {t} | {arm} | {p}/{len(recs)}{f' ({len(recs) - graded} NG)' if len(recs) > graded else ''} | "
                       f"{sum(r['outcome'] == 'OVF' for r in recs)} | {statistics.mean(peaks):,.0f} | "
                       f"{fmt(statistics.mean(r['PFLOPs'] for r in m)) if m else '-'} | "
                       + " ".join(f"r{r['repeat']} {fmt(r['PFLOPs'])} {r['outcome']}" if r["measured"] else f"r{r['repeat']} NG"
                                  for r in recs) + " |")
    # 5. what the context managers did
    out += ["", "## What the context managers did (sums over the 3 repeats)", "",
            "| task | EconoContext: arrival full/slice/preview | evictions | compactions | Jev asked | Jev fallbacks | "
            "CLM-32k edits / rollbacks | CLM-full edits / rollbacks |", "|---|---|---|---|---|---|---|---|"]
    for t in tasks:
        e, c32, cf = actions.get((t, "econo+jev"), {}), actions.get((t, "clm-32k"), {}), actions.get((t, "clm-full"), {})
        errors = f" ({e['jev_errors']})" if e.get("jev_errors") else ""
        out.append(f"| {t} | {e.get('arrival_full_slice_preview') or '0/0/0'} | {e.get('evictions', 0)} | {e.get('compactions', 0)} | "
                   f"{e.get('jev_asked', 0)} | {e.get('jev_fallbacks', 0)}{errors} | "
                   f"{c32.get('clm_edits', '')} / {c32.get('clm_rollbacks', '')} | {cf.get('clm_edits', '')} / {cf.get('clm_rollbacks', '')} |")
    # 6. paired comparisons by task and repeat
    out += ["", "## Paired comparisons with raw, by task and repeat", "",
            "PFLOPs: the arm's trial minus raw's in the same task and repeat (negative = the arm used less), over the pairs "
            "where both trials passed (like-for-like), and over all pairs where both made calls. Outcomes: pairs where both "
            "were graded; the sign test counts the pairs where exactly one of the two passed. Exact two-sided sign tests; "
            "with n = 3 per task these are small samples.", "",
            "| arm vs raw | pairs (both passed) | mean ΔPFLOPs | median Δ | arm lower / higher | sign p | "
            "pairs (both ran) | mean ΔPFLOPs | arm lower / higher | sign p | outcome pairs | arm-only pass / raw-only pass | sign p |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for arm in ARMS[1:]:
        both_pass, both_ran, graded = [], [], []
        for t in tasks:
            for r in repeats:
                a, b = runs.get((t, arm, r)), runs.get((t, "raw", r))
                if not a or not b:
                    continue
                if a["measured"] and b["measured"]:
                    both_ran.append(a["PFLOPs"] - b["PFLOPs"])
                    if a["outcome"] == b["outcome"] == "pass":
                        both_pass.append(a["PFLOPs"] - b["PFLOPs"])
                if a["outcome"] != "NG" and b["outcome"] != "NG":
                    graded.append((a["outcome"] == "pass", b["outcome"] == "pass"))
        lo, hi = sum(d < 0 for d in both_pass), sum(d > 0 for d in both_pass)
        lo2, hi2 = sum(d < 0 for d in both_ran), sum(d > 0 for d in both_ran)
        only_a, only_b = sum(x and not y for x, y in graded), sum(y and not x for x, y in graded)
        out.append(f"| {arm} | {len(both_pass)} | {statistics.mean(both_pass):+.3f} | {statistics.median(both_pass):+.3f} | "
                   f"{lo} / {hi} | {sign_test(lo, hi):.3f} | {len(both_ran)} | {statistics.mean(both_ran):+.3f} | "
                   f"{lo2} / {hi2} | {sign_test(lo2, hi2):.3f} | {len(graded)} | {only_a} / {only_b} | {sign_test(only_a, only_b):.3f} |")
    # 7. checks
    m_all = [r for r in runs.values() if r["measured"]]
    valid, invalid = sum(r["valid"] for r in m_all), sum(r["invalid"] for r in m_all)
    out += ["", "## Checks", "", f"- **Validation:** {valid:,} of {valid + invalid:,} answered calls validated "
            f"({invalid} invalid), over {len(m_all)} measured trials; "
            f"{len(runs) - len(m_all)} trial(s) made no call."]
    rederived = defaultdict(Counter)
    for r in m_all:
        rows = [json.loads(line) for line in (ROOT / r["calls_log"]).read_text().splitlines()]
        ok = [x for x in rows if x.get("call_no") and x.get("status") == 200 and (x.get("usage") or {}).get("completion_tokens") is not None]
        rederived[r["arm"]]["G_server"] += sum(x["usage"]["completion_tokens"] for x in ok)
        rederived[r["arm"]]["P_server"] += sum(x["usage"]["prompt_tokens"] for x in ok)
        rederived[r["arm"]]["calls"] += len(ok)
        rederived[r["arm"]]["G"] += r["G"]
        rederived[r["arm"]]["P"] += r["P"]
        rederived[r["arm"]]["calls_measured"] += r["calls"]
    out += ["- **Totals re-derived from calls.jsonl** (the server's own usage) against measure/ (our rebuild):", "",
            "| arm | answered calls (log / measured) | ΣG server | ΣG measured | ΣP server | ΣP rebuilt | ΣP difference |",
            "|---|---|---|---|---|---|---|"]
    for arm in ARMS:
        d = rederived[arm]
        out.append(f"| {arm} | {d['calls']} / {d['calls_measured']} | {d['G_server']:,} | {d['G']:,} | {d['P_server']:,} | "
                   f"{d['P']:,} | {d['P_server'] - d['P']:+,} |")
    out += ["", "ΣG must match exactly. ΣP differs only by Purdue's random reordering of tool-schema keys, which changes "
            "the served prompt by a few tokens per call (validation accepts it only inside the tools block)."]
    return "\n".join(out) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("label_dir")
    p.add_argument("--out")
    a = p.parse_args()
    text = tables(Path(a.label_dir))
    print(text)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text)


if __name__ == "__main__":
    main()
