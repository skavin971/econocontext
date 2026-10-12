"""Step 11, budget unit (logs only): CLM's own token count (clm_harness.utils.tokens.count_tokens, tiktoken
o200k over the message text) against the exact Qwen prompt (the server's prompt_tokens, which our rebuild
matches), over the CLM calls of tc9 (clm-32k, clm-full) and tc10s (clm-32k). Run in .venv-clm."""
import glob
import json
import statistics

from clm_harness.utils.tokens import count_tokens

LIMIT = 32_000 - 2_048
rows = []
for path in sorted(glob.glob("runs/gateway/tc9.clm-*/calls.jsonl") + glob.glob("runs/gateway/tc10s.clm-*/calls.jsonl")):
    for line in open(path):
        r = json.loads(line)
        if r.get("status") != 200 or not r.get("call_no"):
            continue
        raw, method = count_tokens(r["request"]["messages"])
        label, arm = r["run_id"].split(".")[:2]
        rows.append({"run": r["run_id"], "label": label, "arm": arm, "tiktoken": raw, "qwen": r["usage"]["prompt_tokens"], "method": method})
ratios = [x["qwen"] / x["tiktoken"] for x in rows]
print(f"CLM calls: {len(rows)} (counting method {sorted({x['method'] for x in rows})})")
print(f"Qwen prompt / CLM tiktoken count: median {statistics.median(ratios):.3f}, p10 "
      f"{sorted(ratios)[len(ratios) // 10]:.3f}, p90 {sorted(ratios)[9 * len(ratios) // 10]:.3f}")
for arm in ("clm-32k", "clm-full"):
    sel = [x for x in rows if x["arm"] == arm]
    top = max(sel, key=lambda x: x["qwen"])
    print(f"{arm}: {len(sel)} calls, real Qwen peak {top['qwen']:,} (CLM's tiktoken count {top['tiktoken']:,}) in {top['run']}; "
          f"calls over our 32k limit {LIMIT:,}: {sum(x['qwen'] > LIMIT for x in sel)}")
sel = [x for x in rows if x["arm"] == "clm-32k"]
peak = max(x["qwen"] for x in sel)
print(f"clm-32k real Qwen peak {peak:,} vs our limit {LIMIT:,}: " + ("above" if peak > LIMIT else "not above"))
if peak > LIMIT:
    # CLM gates on its calibrated count > budget - 2,048; the excess comes from its own allowance over the gate.
    print(f"context_budget_tokens matching our limit: {32_000 - (peak - LIMIT):,} (32,000 minus the overshoot {peak - LIMIT:,})")
