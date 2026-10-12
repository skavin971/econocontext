"""Step 12b: per-arm tables from runs/tier-a/step12b/*.csv (step12b_analysis.py), and a cross-check of each
run's PFLOPs against the runs' own report.csv. Writes runs/tier-a/step12b/summary.md.
Use: .venv/bin/python runs/tier-a/scripts/step12b_summary.py"""

import csv
import statistics
from collections import defaultdict
from pathlib import Path

D = Path("runs/tier-a/step12b")
runs = list(csv.DictReader((D / "runs.csv").open()))
calls = list(csv.DictReader((D / "calls.csv").open()))
outputs = list(csv.DictReader((D / "outputs.csv").open()))
loops = list(csv.DictReader((D / "loops.csv").open()))
SOURCES = ("block_tail", "re_prefill", "task_system", "tool_outputs", "own_turns", "other")
f = lambda r, k: float(r[k]) if r[k] not in ("", "None") else None   # noqa: E731

# Cross-check against the runs' own reports
reported = {}
for label in ("tc9", "tc10s", "tc11v"):
    for r in csv.DictReader(open(f"runs/{label}/report.csv")):
        reported[r["run_id"]] = float(r["PFLOPs"])
worst = max(abs(f(r, "PF") - reported[r["run_id"]]) / reported[r["run_id"]] for r in runs)

groups = defaultdict(list)
for r in runs:
    groups[(r["step"], r["arm"])].append(r)
order = sorted(groups, key=lambda g: (["step 9", "10d", "step 11"].index(g[0]), g[1]))
out = [f"# Step 12b tables\n\n{len(runs)} runs, {len(calls)} calls (all validated: "
       f"{sum(int(r['valid']) for r in runs)}), {len(outputs)} tool outputs. Each run's PFLOPs match its "
       f"report.csv (largest relative difference {worst:.1e}).\n"]


def table(title, header, rows):
    out.append(f"\n## {title}\n\n| " + " | ".join(header) + " |\n|" + "---|" * len(header))
    out.extend("| " + " | ".join(str(x) for x in row) + " |" for row in rows)


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "–"


rows = []
for g in order:
    rs = groups[g]
    PF = sum(f(r, "PF") for r in rs)
    rows.append([g[0], g[1], len(rs), f"{PF:.2f}", pct(sum(f(r, 'PF_prefill_new') for r in rs), PF),
                 pct(sum(f(r, 'PF_generated') for r in rs), PF), pct(sum(f(r, 'PF_attn') for r in rs), PF),
                 pct(sum(f(r, 'PF_attn_prefill') for r in rs), PF), pct(sum(f(r, 'PF_attn_decode') for r in rs), PF)])
table("1a. FLOPs split (share of the arm's total PFLOPs)",
      ["step", "arm", "runs", "PFLOPs", "prefill of new tokens", "generated", "attention", "(attn: prefill)", "(attn: decode)"], rows)

rows = []
for g in order:
    rs = groups[g]
    U = sum(f(r, "U") for r in rs)
    rows.append([g[0], g[1], f"{U / len(rs):,.0f}"] + [pct(sum(f(r, f"U_{s}") for r in rs), U) for s in SOURCES])
for row, g in zip(rows, order):
    ids = {r["run_id"] for r in groups[g]}
    re_all = sum(int(c["U_re_prefill"]) for c in calls if c["run_id"] in ids)
    re_sum = sum(int(c["U_re_prefill"]) for c in calls if c["run_id"] in ids and c["kind"] == "summary")
    row.append(pct(re_sum, re_all) if re_all else "–")
table("1b. New prefill tokens U = P - R by source (share of U; mean U per run)",
      ["step", "arm", "U per run", "block tail", "re-prefill after edits", "task + system", "tool outputs",
       "own turns", "other (nudges, notices, summaries, template)", "re-prefill in summary calls"], rows)

rows = []
for g in order:
    rs = groups[g]
    ids = {r["run_id"] for r in rs}
    os_ = [o for o in outputs if o["run_id"] in ids]
    tok = sum(f(o, "tokens") for o in os_)
    rows.append([g[0], g[1], len(os_), f"{tok:,.0f}",
                 pct(sum(f(o, 'visible_lines_8') for o in os_), sum(f(o, 'eligible_lines') for o in os_)),
                 pct(sum(f(o, 'visible_lines_all') for o in os_), sum(f(o, 'lines') for o in os_)),
                 f"{sum(f(o, 'saved_tokens') for o in os_):,.0f}", pct(sum(f(o, 'saved_tokens') for o in os_), tok),
                 sum(1 for o in os_ if f(o, "saved_tokens") > 0)])
table("2. Overlap: tool-output lines already visible when the output arrived",
      ["step", "arm", "outputs", "output tokens", "visible lines (8+ chars)", "visible lines (all non-blank)",
       "tokens saved showing only new lines", "share of output tokens", "outputs with any saving"], rows)

rows = []
for g in order:
    rs = groups[g]
    ids = {r["run_id"] for r in rs}
    big = [o for o in outputs if o["run_id"] in ids and o["big"] == "True"]
    tok = sum(f(o, "tokens") for o in big)
    unused = [o for o in big if o["used"] == "False"]
    rows.append([g[0], g[1], len(big), f"{tok:,.0f}", f"{len(unused)} ({pct(len(unused), len(big))})",
                 f"{sum(f(o, 'tokens') for o in unused):,.0f} ({pct(sum(f(o, 'tokens') for o in unused), tok)})",
                 pct(sum(o["quoted"] == "True" for o in big), len(big)),
                 pct(sum(o["source_path"] == "True" for o in big), len(big)),
                 pct(sum(o["new_path"] == "True" for o in big), len(big))])
table("3. Later use of big outputs (1,000+ tokens as shown)",
      ["step", "arm", "big outputs", "their tokens", "never used again", "tokens never used again",
       "quoted (30+ chars)", "source path named", "a new path from it named"], rows)

rows = []
for g in order:
    rs = groups[g]
    ids = {r["run_id"] for r in rs}
    lp = [x for x in loops if x["run_id"] in ids]
    PF = sum(f(r, "PF") for r in rs)
    rows.append([g[0], g[1], sum(int(r["agent_calls"]) for r in rs),
                 f"{sum(x['kind'] == 'repeat_command' for x in lp)} / {sum(int(r['loop_repeat_command_calls']) for r in rs)}"
                 f" ({sum(int(x['cut_calls']) for x in lp if x['kind'] == 'repeat_command')} cut)",
                 f"{sum(x['kind'] == 'repeat_error' for x in lp)} / {sum(int(r['loop_repeat_error_calls']) for r in rs)}",
                 sum(int(r["loop_extra_calls"]) for r in rs),
                 f"{sum(f(r, 'loop_extra_PF') for r in rs):.3f} ({pct(sum(f(r, 'loop_extra_PF') for r in rs), PF)})"])
table("4. Loops (2+ consecutive identical tool calls, or identical error outputs)",
      ["step", "arm", "agent calls", "command loops / calls in them", "error loops / calls in them",
       "extra calls (after the first in a loop)", "PFLOPs of the extra calls"], rows)
if loops:
    out.append("\nEvery loop:\n" + "\n".join(f"- {x['run_id']}: {x['kind']} from call {x['first_call']}, {x['length']} calls"
                                           f" ({x['cut_calls']} cut at the output cap); calls {x['calls']}" for x in loops))

rows = []
for g in order:
    rs = groups[g]
    ids = {r["run_id"] for r in rs}
    pr = [f(r, "think_P_pearson") for r in rs if f(r, "think_P_pearson") is not None]
    sp = [f(r, "think_P_spearman") for r in rs if f(r, "think_P_spearman") is not None]
    cs = [c for c in calls if c["run_id"] in ids and c["kind"] != "summary"]
    rows.append([g[0], g[1], len(cs), f"{statistics.mean(int(c['think']) for c in cs):.0f}",
                 f"{statistics.mean(int(c['think']) / int(c['G']) for c in cs if int(c['G'])):.2f}", len(pr),
                 f"{statistics.median(pr):+.2f}" if pr else "–", f"{statistics.median(sp):+.2f}" if sp else "–",
                 f"{sum(x > 0 for x in pr)}/{len(pr)}" if pr else "–",
                 f"{sum(x > 0.3 for x in pr)}/{len(pr)}, {sum(x < -0.3 for x in pr)}/{len(pr)}" if pr else "–"])
table("5. Thinking tokens against prompt size P (agent calls; correlation within each run of 5+ calls)",
      ["step", "arm", "calls", "mean thinking tokens", "thinking share of G", "runs with r",
       "median Pearson r", "median Spearman", "runs r > 0", "runs r > 0.3, r < -0.3"], rows)

(D / "summary.md").write_text("\n".join(out) + "\n")
print("\n".join(out))
