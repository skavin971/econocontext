"""Step 12c: per-arm tables from runs/tier-a/step12c/*.csv (step12c_analysis.py). Writes runs/tier-a/step12c/summary.md.
Use: .venv/bin/python runs/tier-a/scripts/step12c_summary.py"""

import csv
from collections import Counter, defaultdict
from pathlib import Path

D = Path("runs/tier-a/step12c")
own = list(csv.DictReader((D / "own_turns.csv").open()))
zone = list(csv.DictReader((D / "zone.csv").open()))
skipped = list(csv.DictReader((D / "zone_skipped.csv").open()))
slices = list(csv.DictReader((D / "slices.csv").open()))
runs = {r["run_id"]: r for r in own}
STEP = {"tc9": "step 9", "tc10s": "10d", "tc11v": "step 11"}
key = lambda run_id: (runs[run_id]["step"], runs[run_id]["arm"])   # noqa: E731
order = sorted({key(r) for r in runs}, key=lambda g: (["step 9", "10d", "step 11"].index(g[0]), g[1]))
pct = lambda a, b: f"{100 * a / b:.1f}%" if b else "–"   # noqa: E731
out = [f"# Step 12c tables\n\n{len(own)} runs. Shares of FLOPs are of the arm's total (Eq. 9, every call).\n"]


def table(title, header, rows, note=None):
    out.append(f"\n## {title}\n\n| " + " | ".join(header) + " |\n|" + "---|" * len(header))
    out.extend("| " + " | ".join(str(x) for x in row) + " |" for row in rows)
    if note:
        out.append("\n" + note)


PARTS = ("visible_text", "write_file_content", "bash_command", "bash_heredoc", "other_args", "glue")
rows = []
for g in order:
    rs = [r for r in own if key(r["run_id"]) == g]
    F = sum(float(r["F_total"]) for r in rs)
    Fo = sum(float(r[f"F_{p}"]) for r in rs for p in PARTS)
    rows.append([g[0], g[1], len(rs), f"{sum(int(r[f'U_{p}']) for r in rs for p in PARTS):,}", pct(Fo, F)] +
                [f"{sum(int(r[f'U_{p}']) for r in rs):,} ({pct(sum(float(r[f'F_{p}']) for r in rs), F)})" for p in PARTS])
table("1. The model's own turns, prefilled again: tokens (share of the arm's FLOPs, linear + prefill attention)",
      ["step", "arm", "runs", "own-turn tokens", "their FLOPs", "visible text", "write_file content",
       "bash command (outside heredocs)", "bash heredoc bodies", "other arguments", "template glue"], rows)

rows = []
for g in order:
    zs = [z for z in zone if key(z["run_id"]) == g]
    rep = Counter(z["status"] for z in zs if z["check"] == "previous reply")
    tool = Counter(z["status"] for z in zs if z["check"] == "tool output")
    rows.append([g[0], g[1], sum(rep.values()), rep["in U"], rep["body in U, header in R"], rep["body in R"],
                 sum(tool.values()), tool["in U"], sum(tool.values()) - tool["in U"],
                 sum(1 for s in skipped if key(s["run_id"]) == g)])
exc = [z for z in zone if z["status"] == "body in R"]
table("2. The free edit zone: the previous reply and the latest tool outputs in U",
      ["step", "arm", "previous replies", "wholly in U", "body in U, header in R", "body in R", "tool outputs",
       "in U", "not in U", "calls with no previous reply"], rows,
      "Exceptions with a body token in R: " + ("; ".join(f"{z['run_id']} call {z['call_no']} (R {z['R']}, body starts "
                                                       f"at token {z['body_first_token']})" for z in exc) or "none"))

rows, rows2 = [], []
for g in order:
    ss = [s for s in slices if key(s["run_id"]) == g]
    if not ss:
        rows.append([g[0], g[1], 0] + ["–"] * 7)
        continue
    tok = sum(int(s["tokens"]) for s in ss)
    ch = sum(int(s["chunks"]) for s in ss)
    used = sum(int(s["used_chunks"]) for s in ss)
    how = Counter(c for s in ss for h in s["used_how"].split() for c in h if c != "-")
    rows.append([g[0], g[1], len(ss), f"{tok:,}", f"{ch} ({sum(int(s['chunks']) == 1 for s in ss)} outputs with 1)",
                 f"{used} ({pct(used, ch)})", f"{sum(int(s['used_chunks']) > 0 for s in ss)}",
                 f"quoted {how['q']}, line {how['l']}, symbol {how['s']}",
                 f"{sum(int(s['oracle_saved']) for s in ss):,} ({pct(sum(int(s['oracle_saved']) for s in ss), tok)})",
                 sum(s["query_fallback"] == "True" for s in ss)])
    rows2.append([g[0], g[1], used] + [
        f"{pct(sum(int(s[f'bm25_{f}_saved']) for s in ss), tok)}; missed {sum(int(s[f'bm25_{f}_missed']) for s in ss)}"
        f" ({sum(int(s[f'bm25_{f}_missed']) > 0 for s in ss)} outputs)" for f in (25, 50, 75)])
table("3a. The slicing upper bound: big outputs (1,000+ tokens), 40-line chunks, the oracle slice",
      ["step", "arm", "big outputs", "tokens", "chunks", "later-used chunks", "outputs with a used chunk",
       "used by (a chunk may count twice)", "oracle slice saves", "BM25 query fell back to the call's arguments"], rows)
table("3b. BM25 slices (top chunks against the agent's last visible text): tokens saved; later-used chunks dropped",
      ["step", "arm", "later-used chunks", "keep up to 25%", "keep up to 50%", "keep up to 75%"], rows2)

(D / "summary.md").write_text("\n".join(out) + "\n")
print("\n".join(out))
