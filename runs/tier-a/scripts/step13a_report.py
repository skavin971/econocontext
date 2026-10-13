"""Step 13a: the pilot's numbers, per arm, from ~/econo/bcp/runs/<label> (summaries, call logs, the judge's verdicts).
Only numbers are written to the repo (runs/tier-a/step13a/), never BrowseComp-Plus text.

Per arm: accuracy (judged, and exact match), calls, searches and document reads per question, PFLOPs per question
(Eq. 9 on the validated calls; judge calls excluded: they have their own run id), peak prompt in Qwen tokens, CLM
edits (n_ctx_syncs_real) or compactions, wall time, validation; and a projection for 150 questions x 3 arms x 3
repeats under the gateway's 8,000 requests a day and Purdue's 20 a minute.

Use: PYTHONPATH=. .venv/bin/python runs/tier-a/scripts/step13a_report.py [--label bcp13p]
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import step12b_analysis as b  # noqa: E402

from measure.flops import measure_log  # noqa: E402
from measure.models import MEASURED, load  # noqa: E402
from measure.tokens import tokenizer  # noqa: E402

BCP = Path.home() / "econo" / "bcp"
REJECTED = ("Not run:",)


def tool_counts(calls_log: Path) -> tuple[int, int]:
    """Searches and document reads that ran: tool results (first shown) of bcp_search / bcp_get_document calls,
    called as tools (our harness) or as commands (CLM's skill), excluding calls the interface did not run."""
    rows = [json.loads(x) for x in calls_log.read_text().split("\n") if x]
    rows = [r for r in rows if r.get("call_no") and r.get("status") == 200 and r.get("kind", "agent") == "agent"]
    producer, seen, searches, reads = {}, set(), 0, 0
    for r in rows:
        for m in r["request"]["messages"]:
            for c in m.get("tool_calls") or []:
                fn = c.get("function") or {}
                producer[c.get("id")] = (fn.get("name") or "", fn.get("arguments") or "")
            if m.get("role") == "tool" and m.get("tool_call_id") not in seen:
                seen.add(m.get("tool_call_id"))
                if b.text_of(m.get("content")).startswith(REJECTED):
                    continue
                name, args = producer.get(m.get("tool_call_id"), ("", ""))
                searches += name == "bcp_search" or (name == "bash" and "bcp_search" in args)
                reads += name == "bcp_get_document" or (name == "bash" and "bcp_get_document" in args)
    return searches, reads


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="bcp13p")
    a = ap.parse_args()
    base_dir = BCP / "runs" / a.label
    k = load(MEASURED)
    tok = tokenizer(MEASURED)
    verdicts = {}
    if (base_dir / "judge.jsonl").exists():
        for x in (base_dir / "judge.jsonl").read_text().split("\n"):
            if x:
                v = json.loads(x)
                verdicts[v["run_id"]] = v
    per = defaultdict(list)
    for path in sorted(base_dir.glob("*/*/summary.json")):
        s = json.loads(path.read_text())
        if s.get("skipped"):
            continue
        log = Path(s["calls_log"])
        m = measure_log(log, tok, k["C_token"], k["C_attn"]) if log.exists() else {"totals": {}}
        t = m["totals"]
        searches, reads = tool_counts(log) if log.exists() else (0, 0)
        v = verdicts.get(s["run_id"], {})
        per[s["arm"]].append({
            "run_id": s["run_id"], "query_id": s["task"], "calls": t.get("calls", 0), "valid": t.get("valid", 0),
            "invalid": t.get("invalid", 0), "PF": t.get("F", 0) / 1e15, "peak_P": s.get("peak_P"),
            "searches": searches, "reads": reads, "seconds": s.get("seconds"), "end": s.get("end"),
            "exception": s.get("exception"), "clm_edits": (s.get("clm") or {}).get("n_ctx_syncs_real"),
            "clm_rollbacks": (s.get("clm") or {}).get("n_retry_on_limit"),
            "compactions": len(s.get("compactions") or []),
            "judged": v.get("judged_correct"), "judge_parsed": v.get("judge_parsed"),
            "exact": v.get("exact_match"), "answer_file": v.get("has_answer_file")})
    out_dir = ROOT / "runs" / "tier-a" / "step13a"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [r for arm in per for r in per[arm]]
    with (out_dir / "pilot.jsonl").open("w") as f:
        for arm in per:
            for r in per[arm]:
                f.write(json.dumps({"arm": arm, **r}) + "\n")
    mean = lambda v: statistics.mean(v) if v else 0   # noqa: E731
    lines = ["| arm | questions | judged correct | exact match | answer files | calls / q | searches / q | reads / q | "
             "PFLOPs / q | peak prompt (max) | CLM edits / rollbacks per q | wall time / q | calls valid |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for arm, rs in sorted(per.items()):
        lines.append(
            f"| {arm} | {len(rs)} | {sum(bool(r['judged']) for r in rs)}/{len(rs)} | {sum(bool(r['exact']) for r in rs)}/{len(rs)} | "
            f"{sum(bool(r['answer_file']) for r in rs)}/{len(rs)} | {mean([r['calls'] for r in rs]):.1f} | "
            f"{mean([r['searches'] for r in rs]):.1f} | {mean([r['reads'] for r in rs]):.1f} | {mean([r['PF'] for r in rs]):.3f} | "
            f"{max((r['peak_P'] or 0) for r in rs):,} | "
            + (f"{mean([r['clm_edits'] or 0 for r in rs]):.1f} / {mean([r['clm_rollbacks'] or 0 for r in rs]):.1f}" if arm == "clm"
               else f"– (compactions {sum(r['compactions'] for r in rs)})")
            + f" | {mean([r['seconds'] or 0 for r in rs]) / 60:.1f} min | {sum(r['valid'] for r in rs)}/{sum(r['valid'] + r['invalid'] for r in rs)} |")
    calls_per_q = mean([r["calls"] for r in rows])
    minutes_per_q = mean([(r["seconds"] or 0) / 60 for r in rows])
    runs = 150 * 3 * 3
    total_calls = runs * calls_per_q + runs           # plus one judge call per answer
    lines += ["", f"Projection for 150 questions x 3 arms x 3 repeats = {runs:,} runs, at the pilot's mean of "
              f"{calls_per_q:.1f} calls and {minutes_per_q:.1f} minutes per run: about {total_calls:,.0f} requests "
              f"(judge included), so {total_calls / 8000:.1f} days at the gateway's 8,000 requests a day "
              f"({total_calls / 20 / 60 / 24:.1f} days at Purdue's 20 a minute if the cap allowed it); "
              f"{runs * minutes_per_q / 60 / 24:.1f} days of trials run one at a time."]
    (out_dir / "pilot_summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    for arm, rs in sorted(per.items()):
        for r in rs:
            print(f"  {r['run_id']}: calls {r['calls']} (valid {r['valid']}), searches {r['searches']}, reads {r['reads']}, "
                  f"peak {r['peak_P']}, {r['PF']:.3f} PF, {(r['seconds'] or 0) / 60:.1f} min, end {r['end']}, "
                  f"exception {r['exception']}, judged {r['judged']}, exact {r['exact']}")


if __name__ == "__main__":
    main()
