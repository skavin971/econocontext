"""Step 18: the report's tables, from runs/tier-a/step18/instances.csv (step18_complexity_trap.py).
FIXED-TRAJECTORY ESTIMATES on the canonical track. Writes runs/tier-a/step18/summary.md.
Use: .venv/bin/python runs/tier-a/scripts/step18_summary.py"""

import csv
import statistics
from collections import defaultdict
from pathlib import Path

D = Path("runs/tier-a/step18")
rows = list(csv.DictReader((D / "instances.csv").open()))
f = lambda r, k: float(r[k] or 0)   # noqa: E731
ARMS = ("raw", "masking", "summary")
by = defaultdict(dict)
for r in rows:
    by[r["arm"]][r["instance"]] = r
bad = {arm: [i for i, r in by[arm].items() if r["unreadable"] == "True"] for arm in ARMS}
common = sorted(set.intersection(*[{i for i, r in by[a].items() if r["unreadable"] != "True"} for a in ARMS]))
METRICS = [("their_cost", "their reported instance cost ($)"), ("sent", "tokens sent (Qwen)"),
           ("usd_nodisc", "dollars, no cache-read discount"), ("usd", "dollars, their cache-read price (40% of input)"),
           ("usd_10", "dollars, cache read at 10% of input"),
           ("F", "FLOPs (Eq. 9)"), ("new", "new-prefill tokens"), ("G", "generated tokens"), ("calls", "calls"),
           ("turns", "agent turns")]
out = ["# Step 18 tables (fixed-trajectory estimates, canonical track)", "",
       f"Qwen3-Coder-480B on SWE-bench Verified, SWE-agent. Trajectories: {', '.join(f'{a} {len(by[a])}' for a in ARMS)}; "
       f"unreadable .traj files: {', '.join(f'{a} {len(bad[a])}' for a in ARMS)}. Paired on the {len(common)} instances readable "
       "in all three arms.", "",
       "| metric | raw | masking | summary | masking vs raw | summary vs raw |", "|---|---|---|---|---|---|"]
tot = {a: {k: sum(f(by[a][i], k) for i in common) for k, _ in METRICS} for a in ARMS}
for k, name in METRICS:
    t = [tot[a][k] for a in ARMS]
    fmt = (lambda v: f"${v:,.2f}") if k in ("their_cost", "usd", "usd_10", "usd_nodisc") else (lambda v: f"{v / 1e15:,.0f} PF") if k == "F" else (lambda v: f"{v / 1e6:,.1f}M" if v > 1e6 else f"{v:,.0f}")
    out.append(f"| {name} | {fmt(t[0])} | {fmt(t[1])} | {fmt(t[2])} | **{100 * (t[1] / t[0] - 1):+.1f}%** | **{100 * (t[2] / t[0] - 1):+.1f}%** |")
raw = tot["raw"]
cf = {k: sum(f(by["raw"][i], f"cf_{k}") for i in common) for k in ("sent", "usd_nodisc", "usd", "usd_10", "F", "new")}
out += ["", "## Same trajectories: their masking rule applied by us to the raw arm's own prompts", "",
        "| metric | raw as run | raw, masked | change |", "|---|---|---|---|"]
for k, name in [("sent", "tokens sent"), ("usd_nodisc", "dollars, no discount"), ("usd", "dollars, cache-read price"),
                ("usd_10", "dollars, cache read at 10%"), ("F", "FLOPs"), ("new", "new-prefill tokens")]:
    out.append(f"| {name} | {raw[k]:,.4g} | {cf[k]:,.4g} | **{100 * (cf[k] / raw[k] - 1):+.1f}%** |")
# per-instance: how often is masking cheaper, paired by instance
out += ["", "## Per instance (paired): share of instances where the masking arm is cheaper than raw", ""]
for k, name in [("their_cost", "their cost"), ("sent", "tokens sent"), ("usd", "dollars, cache-read price"),
                ("usd_10", "dollars, cache read at 10%"), ("F", "FLOPs")]:
    wins = sum(f(by["masking"][i], k) < f(by["raw"][i], k) for i in common)
    ratio = statistics.median(f(by["masking"][i], k) / f(by["raw"][i], k) for i in common if f(by["raw"][i], k) > 0)
    out.append(f"- {name}: {wins} of {len(common)} ({100 * wins / len(common):.0f}%); median masking/raw ratio {ratio:.2f}")
wins_cf = sum(f(by["raw"][i], "cf_F") < f(by["raw"][i], "F") for i in common)
out.append(f"- same trajectory, FLOPs: masked raw cheaper than raw in {wins_cf} of {len(common)}")
check = sum(f(by[a][i], "sent") for a in ARMS for i in common) / sum(f(by[a][i], "their_raw_input") + f(by[a][i], "their_cached_input") for a in ARMS for i in common)
cache_theirs = {a: sum(f(by[a][i], "their_cached_input") for i in common) / max(1, sum(f(by[a][i], "their_raw_input") + f(by[a][i], "their_cached_input") for i in common)) for a in ARMS}
cache_ours = {a: 1 - tot[a]["new"] / tot[a]["sent"] for a in ARMS}
out += ["", f"Check: our input tokens are {100 * check:.1f}% of theirs (their token_counter, Qwen3-Coder's tokenizer; ours adds message wrappers). "
        f"Cached share of input, theirs (message-level prefix with the previous prompt) vs ours (prefix cache over all earlier prompts): "
        + ", ".join(f"{a} {100 * cache_theirs[a]:.1f}% vs {100 * cache_ours[a]:.1f}%" for a in ARMS) + "."]
(D / "summary.md").write_text("\n".join(out) + "\n")
print("\n".join(out))
