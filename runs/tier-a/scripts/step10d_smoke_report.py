"""Step 10d smoke report from the logs: runs/tc10s summaries, the gateway's calls.jsonl, measure's calls.csv."""
import csv
import glob
import json
import sys
from pathlib import Path

LABEL = sys.argv[1] if len(sys.argv) > 1 else "tc10s"
ROOT = Path.cwd()
calls_csv = {(r["run_id"], int(r["call_no"])): r for r in csv.DictReader(open(ROOT / "runs" / LABEL / "calls.csv"))}
report = {r["run_id"]: r for r in csv.DictReader(open(ROOT / "runs" / LABEL / "report.csv"))}


def new_messages(before, after):
    """Messages in `after` that were not at the end of `before` (what was added between two requests)."""
    n = 0
    while n < min(len(before), len(after)) and before[n] == after[n]:
        n += 1
    return after[n:]


def short(m, n=150):
    if m.get("role") == "assistant":
        calls = [(c["function"]["name"], c["function"]["arguments"][:80]) for c in m.get("tool_calls") or []]
        return f"assistant: content {str(m.get('content'))[:60]!r}, tool calls {calls}"
    return f"{m.get('role')}: {str(m.get('content'))[:n]!r}"


print(f"== {LABEL}: per run")
for path in sorted(glob.glob(str(ROOT / "runs" / LABEL / "*" / "*" / "summary.json"))):
    s = json.load(open(path))
    rid = s["run_id"]
    rows = [json.loads(l) for l in open(ROOT / s["calls_log"])]
    m = report.get(rid, {})
    checks = [calls_csv[(rid, r["call_no"])] for r in rows if (rid, r["call_no"]) in calls_csv]
    valid = sum(c["validation"].startswith(("identical", "tools block only")) for c in checks)
    print(f"\n{rid}: {'pass' if s.get('reward') == 1.0 else 'fail' if s.get('reward') is not None else 'not graded'} "
          f"(reward {s.get('reward')}), end {s.get('end')}, calls {s['calls']} {s.get('calls_by_kind')}, "
          f"PFLOPs {float(m.get('PFLOPs', 'nan')):.3f}, validated {valid}/{len(checks)}, calls at the cap "
          f"{sum((r.get('usage') or {}).get('completion_tokens', 0) >= (r['request'].get('max_tokens') or 10**9) for r in rows)}")
    if s.get("final_turns"):
        print(f"   final turns (CLM's rule): {s['final_turns']}; end_P {s.get('end_P')}")
    if s.get("clm"):
        c = s["clm"]
        print(f"   CLM: context edits {c.get('n_ctx_syncs_real')} (rejected {c.get('n_ctx_rejected')}), nudges {c.get('n_nudges')}, "
              f"rollbacks {c.get('n_retry_on_limit')}, budget_finalized {c.get('budget_finalized')}")
    for k, comp in enumerate(s.get("compactions") or [], 1):
        call_no = comp["summary_call"]
        nxt = calls_csv.get((rid, call_no + 1), {})
        summ = calls_csv.get((rid, call_no), {})
        print(f"   compaction {k}: P before {comp['P_before']:,}, compaction call P {comp['P_call']:,} "
              f"(server {summ.get('P', '?')}), summary G {comp['G']} (capped {comp['capped']}, {comp['finish_reason']}), "
              f"P after {comp['P_after']:,} = {comp['P_after_share_of_budget']:.1%} of the budget; summary call valid: "
              f"{summ.get('validation')}; first call after: #{call_no + 1} P {nxt.get('P')} {nxt.get('validation')}")
    bad = [c for c in checks if not c["validation"].startswith(("identical", "tools block only"))]
    for c in bad:
        print(f"   INVALID call {c['call_no']}: {c['validation']}")

print(f"\n== one call at the output cap per arm, and what the agent did next")
by_arm = {}
for path in sorted(glob.glob(str(ROOT / "runs" / "gateway" / f"{LABEL}.*" / "calls.jsonl"))):
    rows = [json.loads(l) for l in open(path)]
    arm = rows[0]["run_id"].split(".")[1]
    for i, r in enumerate(rows):
        u, cap = r.get("usage") or {}, r["request"].get("max_tokens")
        if cap and u.get("completion_tokens", 0) >= cap and arm not in by_arm:
            by_arm[arm] = (r, rows[i + 1] if i + 1 < len(rows) else None)
for arm in ("raw-32k", "summary-32k", "clm-32k"):
    if arm not in by_arm:
        print(f"\n{arm}: no call hit the cap")
        continue
    r, nxt = by_arm[arm]
    ch = r["response"]["choices"][0]
    msg = ch["message"]
    print(f"\n{arm}: {r['run_id']} call {r['call_no']} ({r['kind']}): completion_tokens {r['usage']['completion_tokens']} = max_tokens "
          f"{r['request']['max_tokens']}, finish_reason {ch['finish_reason']}, reasoning chars "
          f"{len(msg.get('reasoning_content') or msg.get('reasoning') or '')}, content chars {len(msg.get('content') or '')}, "
          f"tool calls {[(c['function']['name'], c['function']['arguments'][:100]) for c in msg.get('tool_calls') or []]}")
    if nxt is None:
        print("   no later call: the run ended")
        continue
    added = new_messages(r["request"]["messages"], nxt["request"]["messages"])
    print(f"   next call {nxt['call_no']} ({nxt['kind']}) adds {len(added)} message(s):")
    for m in added[:4]:
        print(f"     {short(m)}")
