"""Step 19: the report's tables, from runs/tier-a/step19/<model>_{instances.csv,lengths.json} (step19_variants.py).
FIXED-TRAJECTORY ESTIMATES on the canonical track. Writes runs/tier-a/step19/summary.md.
Use: .venv/bin/python runs/tier-a/scripts/step19_summary.py"""

import csv
import json
from pathlib import Path

D = Path("runs/tier-a/step19")
NAMES = {"coder": "Qwen3-Coder-480B", "qwen32b": "Qwen3-32B", "gemini": "Gemini 2.5 Flash"}
SWEEP = (0, 5, 10, 13, 20, 25, 40, 50, 100)
out = ["# Step 19 tables (fixed-trajectory estimates, canonical track)", ""]
lead = []
for model, mname in NAMES.items():
    path = D / f"{model}_instances.csv"
    if not path.exists():
        continue
    rows = list(csv.DictReader(path.open()))
    lens = json.loads((D / f"{model}_lengths.json").read_text())
    ok = [r for r in rows if all(r.get(f"{a}|unreadable") == "False" for a in ("raw", "masking", "summary")) and r.get("prefix_ok") == "True"]
    f = lambda r, k: float(r.get(k) or 0)   # noqa: E731
    tot = lambda key, k: sum(f(r, f"{key}|{k}") for r in ok)   # noqa: E731
    usd = lambda key, c: tot(key, "A") + c * tot(key, "B")   # noqa: E731
    out += [f"## {mname}: {len(ok)} paired instances (of {len(rows)}; dropped: unreadable or not append-only)", "",
            "### 1. Cost ratio against raw, by cache-read price (share of the input price)", "",
            "| cache read | " + " | ".join(f"{c}%" for c in SWEEP) + " |", "|---|" + "---|" * len(SWEEP)]
    for key, label in (("masking", "masking / raw (arms as run)"), ("summary", "summary / raw (arms as run)"),
                       ("cf masking", "masking / raw (same trajectory)")):
        out.append(f"| {label} | " + " | ".join(f"{usd(key, c / 100) / usd('raw', c / 100):.2f}" for c in SWEEP) + " |")
    out += ["", "### 2. Variants on the raw arm's trajectory, with the arms as run", "",
            "| policy | tokens sent | FLOPs | $ at 10% | $ at 25% | $ at 40% | median prompt | p90 prompt | replies needing a stubbed observation | restores |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    keys = ["raw", "masking", "summary", "cf masking"] + sorted({k.split("|")[0] for k in rows[0] if k.startswith("cf V")})
    label = {"raw": "raw (as run)", "masking": "masking (arm as run)", "summary": "LLM-Summary (arm as run)",
             "cf masking": "masking, same trajectory"}
    stats = {}
    for key in keys:
        L = lens.get(key) or {}
        stats[key] = {"F": tot(key, "F"), "u10": usd(key, 0.1), "u25": usd(key, 0.25), "u40": usd(key, 0.4),
                      "sent": tot(key, "sent"), "med": L.get("median"), "p90": L.get("p90")}
        s = stats[key]
        out.append(f"| {label.get(key, key[3:])} | {s['sent'] / 1e6:,.1f}M | {s['F'] / 1e15:,.0f} PF | ${s['u10']:,.2f} | ${s['u25']:,.2f} | "
                   f"${s['u40']:,.2f} | {s['med']:,.0f} | {s['p90']:,.0f} | {tot(key, 'lost'):,.0f} | {tot(key, 'restores'):,.0f} |")
    out.append("")
    for key in keys[4:]:
        s = stats[key]
        beats = []
        for ref in ("raw", "masking", "cf masking"):
            r = stats[ref]
            beats.append(all(s[m] < r[m] for m in ("F", "u10", "u40")))
        short = s["med"] <= 1.2 * stats["masking"]["med"]
        short_cf = s["med"] <= 1.2 * stats["cf masking"]["med"]
        lead.append((mname, key[3:], beats[0], beats[1], beats[2], short, short_cf,
                     s["F"] / stats["raw"]["F"], s["u10"] / stats["raw"]["u10"], s["u40"] / stats["raw"]["u40"],
                     s["med"] / stats["masking"]["med"], tot(key, "lost")))
out += ["## Lead: does a variant beat raw and masking in FLOPs and in dollars at 10% and 40%, with a median prompt within 20% of masking's?", "",
        "| model | variant | beats raw | beats masking arm | beats masking, same trajectory | median within 20% of masking arm | of masking, same trajectory | FLOPs / raw | $10 / raw | $40 / raw | median / masking arm | replies needing a stubbed observation |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|"]
for m, v, b1, b2, b3, s1, s2, fr, u10, u40, med, lost in lead:
    yn = lambda x: "**yes**" if x else "no"   # noqa: E731
    out.append(f"| {m} | {v} | {yn(b1)} | {yn(b2)} | {yn(b3)} | {yn(s1)} | {yn(s2)} | {fr:.2f} | {u10:.2f} | {u40:.2f} | {med:.2f} | {lost:,.0f} |")
(D / "summary.md").write_text("\n".join(out) + "\n")
print("\n".join(out))
