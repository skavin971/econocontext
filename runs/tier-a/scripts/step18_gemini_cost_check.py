"""Step 18: was a cache-read discount applied to The Complexity Trap's Gemini costs? Offline, no model calls.

Their Gemini runs price each call with litellm.cost_calculator.completion_cost(response) (sweagent/agent/models.py),
which applies the cache-read price only to cached tokens the provider reports; the responses are not in the released
logs. So the check is indirect: per turn, the logged cost (turn_statistics.cost) against their own token counts
(raw_input, cached_input, output + internal_reasoning) priced with litellm 1.77's gemini-2.5-flash card ($ per token:
input 3e-7, output 2.5e-6, cache read 7.5e-8), without and with the discount, binned by their cached share. With a
discount applied, turns that are 90-100% cached would cost about 0.25-0.3 of the no-discount price.

Data: ~/econo/ctrap/data/gemini_mask.tar.gz (sweagent/gemini_2.5_flash-agent-t_0.8-baseline_N_1_M_10.0-verified-500,
0.31 GB, streamed). Use: python3 -I runs/tier-a/scripts/step18_gemini_cost_check.py
"""

import json
import statistics
import tarfile
from pathlib import Path

ARCHIVE = Path.home() / "econo" / "ctrap" / "data" / "gemini_mask.tar.gz"
IN, OUT, CACHE = 3e-7, 2.5e-6, 7.5e-8

bins, n_turns, tot = {}, 0, {"logged": 0.0, "nodisc": 0.0, "disc": 0.0}
with tarfile.open(ARCHIVE, "r|gz") as t:
    for m in t:
        if not m.name.endswith(".traj"):
            continue
        try:
            d = json.load(t.extractfile(m))
        except ValueError:
            continue
        for turn in d["trajectory"]:
            st = turn.get("turn_statistics") or {}
            tk = st.get("tokens") or {}
            if not tk or st.get("cost") is None:
                continue
            raw, cached = tk.get("raw_input", 0), tk.get("cached_input", 0)
            out = tk.get("output", 0) + (tk.get("internal_reasoning") or 0)
            nodisc = (raw + cached) * IN + out * OUT
            if nodisc <= 0:
                continue
            bins.setdefault(min(9, int(10 * cached / max(1, raw + cached))), []).append(st["cost"] / nodisc)
            n_turns += 1
            tot["logged"] += st["cost"]
            tot["nodisc"] += nodisc
            tot["disc"] += raw * IN + cached * CACHE + out * OUT
print(f"turns {n_turns}: logged cost ${tot['logged']:.2f}; their tokens priced without discount ${tot['nodisc']:.2f}, "
      f"with litellm's cache-read price ${tot['disc']:.2f}")
for b in sorted(bins):
    v = bins[b]
    print(f"cached share {10 * b:2d}-{10 * b + 10:3d}%: {len(v):6d} turns, median logged / no-discount price {statistics.median(v):.3f}")
