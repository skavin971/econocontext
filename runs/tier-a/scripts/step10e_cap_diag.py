"""Step 10e: per-call output tokens, thinking vs visible, and cap hits, from the gateway's call logs."""
import csv
import glob
import json
import statistics
import sys
from pathlib import Path

OUT = Path(sys.argv[1])
rows = []
for path in sorted(glob.glob("runs/gateway/tc10s.*bandit*/calls.jsonl") + glob.glob("runs/gateway/tc9.*/calls.jsonl")):
    for line in open(path):
        r = json.loads(line)
        if r.get("status") != 200 or not r.get("call_no"):
            continue
        u = r.get("usage") or {}
        G = u.get("completion_tokens") or 0
        think = (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        ch = r["response"]["choices"][0]
        m = ch.get("message") or {}
        label, arm, task, rep = r["run_id"].split(".")
        rows.append({"label": label, "arm": arm, "task": task, "repeat": rep, "call": r["call_no"], "kind": r["kind"],
                     "max_tokens": r["request"].get("max_tokens"), "G": G, "thinking": think, "visible": G - think,
                     "capped": int(G >= (r["request"].get("max_tokens") or 10**9)), "finish": ch.get("finish_reason"),
                     "tool_call": int(bool(m.get("tool_calls"))), "content_chars": len(m.get("content") or ""),
                     "P": u.get("prompt_tokens")})
with (OUT / "step10e-calls.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else 0


def table(sel, title):
    print(f"\n{title}")
    print(f"{'arm':12s} {'calls':>5s} | {'G med':>5s} {'G p90':>5s} | {'think med':>9s} {'think p90':>9s} | {'vis med':>7s} {'vis p90':>7s} | "
          f"{'at cap':>9s} | {'cut thinking':>12s} {'cut w/ call':>11s}")
    for arm in sorted({r["arm"] for r in sel}, key=lambda a: ("clm" in a, a)):
        rs = [r for r in sel if r["arm"] == arm and r["kind"] == "agent"]
        cap = [r for r in rs if r["capped"]]
        print(f"{arm:12s} {len(rs):5d} | {statistics.median(r['G'] for r in rs):5.0f} {pct([r['G'] for r in rs], .9):5d} | "
              f"{statistics.median(r['thinking'] for r in rs):9.0f} {pct([r['thinking'] for r in rs], .9):9d} | "
              f"{statistics.median(r['visible'] for r in rs):7.0f} {pct([r['visible'] for r in rs], .9):7d} | "
              f"{len(cap):3d} ({len(cap) / len(rs):4.0%}) | {sum(not r['tool_call'] for r in cap):12d} {sum(r['tool_call'] for r in cap):11d}")


table([r for r in rows if r["label"] == "tc10s"], "tc10s, bandit at 32k, max_tokens 2,048 (agent calls; G = output tokens = thinking + visible)")
table([r for r in rows if r["label"] == "tc9" and r["task"] == "bandit-delayed-feedback"], "tc9, bandit, 3 repeats, max_tokens 8,192")
table([r for r in rows if r["label"] == "tc9"], "tc9, all 10 tasks x 3 repeats, max_tokens 8,192")
t9 = [r for r in rows if r["label"] == "tc9" and r["kind"] == "agent"]
print("\ntc9: share of agent calls whose output would not fit in 2,048 (G > 2,048), and whose thinking alone would not:")
for arm in ("raw", "econo+jev", "clm-32k", "clm-full"):
    rs = [r for r in t9 if r["arm"] == arm]
    print(f"  {arm:10s} G > 2,048: {sum(r['G'] > 2048 for r in rs) / len(rs):5.1%}   thinking > 2,048: "
          f"{sum(r['thinking'] > 2048 for r in rs) / len(rs):5.1%}   visible > 2,048: {sum(r['visible'] > 2048 for r in rs) / len(rs):5.1%}")
print(f"\nwrote {OUT / 'step10e-calls.csv'} ({len(rows)} calls)")
