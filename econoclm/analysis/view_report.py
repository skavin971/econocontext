"""EconoCLM-View report: how the model used VIEW.md, plus the gate checks. Offline.

  python -m econoclm.analysis.view_report runs/<phase>     # writes view_report.md

Per EconoCLM-View run (<run>/view_log.jsonl, view_versions/, view_turns.json, econo.sqlite):
model edits of VIEW.md (never the automatic `turn K` appends), rejected edits, lines
dropped and restored, notes written, obs lines added, `econo get` / `econo search` use,
peak prompt (gateway ledger), stale flags followed, and where in the view edits happened
(first changed line as a share of the view). Gate checks (REPORT.md deviation 9):
crashes and hook errors, edits in at least 5 of 10 runs, at least 6 of 10 passes, and
determinism (every logged view re-rendered from the saved turn store and outputs must
give the logged sha256). It also checks that the system prompts are CLM's (CLM arm),
CLM's plus the SKILL text (EconoCLM-Tools), and CLM's with only the section swapped
(EconoCLM-View), and shows 3 real VIEW.md files.
"""

import argparse
import hashlib
import json
import statistics
from pathlib import Path

from ..arms.econo_view.agent import TEXT_VIEW, swap_section
from ..arms.econo_view.view import parse, render, render_bytes
from .common import RETRY_REASONS, load_runs
from .results_table import row_for


def first_changed_line(old: list[str], new: list[str]) -> int:
    for i, (a, b) in enumerate(zip(old, new)):
        if a != b:
            return i
    return min(len(old), len(new))


def view_runs(day: Path) -> list[dict]:
    out = []
    for run in load_runs(day, {"econoview"}):
        d = run.dir
        log = [json.loads(x) for x in (d / "view_log.jsonl").read_text().splitlines()] \
            if (d / "view_log.jsonl").exists() else []
        turns = json.loads((d / "view_turns.json").read_text()) if (d / "view_turns.json").exists() else {}
        turns = {int(k): v for k, v in turns.items()}

        def obs(n, d=d):
            p = d / "obs" / f"{n}.txt"
            return p.read_bytes().decode("utf-8", errors="surrogateescape") if p.exists() else None

        det_ok = det_n = 0
        for r in log:
            lines = parse("\n".join(r["lines"])).lines
            if hashlib.sha256(render_bytes(render(lines, turns, obs).messages)).hexdigest() == r["sha256"]:
                det_ok += 1
            det_n += 1
        edits = [r for r in log if r.get("kind") == "edit"]
        positions, prev = [], None
        for r in log:
            if r.get("kind") == "edit" and prev is not None:
                positions.append(first_changed_line(prev, r["lines"]) / max(1, len(prev)))
            prev = r["lines"]
        notes = sorted({n for r in edits for n in r.get("notes", [])})
        obs_lines = sorted({x for r in log for x in r["lines"] if x.startswith("obs ")})
        store = run.store()
        ops = {o["op"]: o["n"] for o in store.rows("SELECT op, COUNT(*) n FROM econo_ops GROUP BY op")} if store else {}
        hook_errors = store.rows("SELECT COUNT(*) n FROM hook_errors")[0]["n"] if store else None
        row = row_for(run)
        calls = [c for c in run.calls if c["finish_reason"] not in RETRY_REASONS]
        out.append(dict(
            task=run.task, passed=row["passed"], exception=row["exception"], cost=row["cost_usd"],
            calls=len(calls), model_edits=len(edits),
            rejected=sum(r.get("kind") == "rejected" for r in log),
            appends=sum(r.get("appended", 0) for r in log),
            dropped=sum(len(r.get("dropped", [])) for r in edits),
            ignored_receipts=sum(bool(r.get("bad")) for r in log if r.get("kind") in ("edit", "same", "rejected")),
            from_line1=sum(r.get("first_changed_line") == 0 for r in edits),
            note_lines=max([n for r in edits for n in (r.get("note_lines") or {}).values()] or [0]),
            restored=sum(len(r.get("restored", [])) for r in edits),
            notes=notes, obs_lines=obs_lines, econo_get=ops.get("get", 0), econo_search=ops.get("search", 0),
            peak_prompt=max((c["prompt_tokens"] or 0) for c in calls) if calls else 0,
            stale=row.get("stale_flags", 0), stale_followed=row.get("stale_flags_reread", 0),
            hook_errors=hook_errors, det=(det_ok, det_n), positions=positions,
            versions=sorted((d / "view_versions").glob("*.md")) if (d / "view_versions").exists() else []))
    return out


def system_prompt_check(day: Path, baseline: Path | None = None) -> list[str]:
    base_day = baseline or day

    def first_system(run_id, d=day):
        f = d / "bodies" / run_id / "0000.request.json"
        if not f.exists() and d is not base_day:
            f = base_day / "bodies" / run_id / "0000.request.json"
        return json.loads(f.read_text())["messages"][0]["content"] if f.exists() else None
    runs = load_runs(day) + ([r for r in load_runs(base_day) if r.arm != "econoview"] if baseline else [])
    clm = {r.task: first_system(r.run_id, base_day) for r in runs if r.arm == "raw"}
    lines = []
    for arm, label in (("raw", "CLM"), ("econo12", "EconoCLM-Tools"), ("econoview", "EconoCLM-View")):
        ok = n = 0
        for r in runs:
            if r.arm != arm or clm.get(r.task) is None:
                continue
            s, base = first_system(r.run_id, day if r.arm == "econoview" else base_day), clm[r.task]
            if s is None:
                continue
            n += 1
            if arm == "raw":
                ok += "## Managing your context" in s and "LIVE_CTX_MAIN.txt" in s
            elif arm == "econo12":
                ok += s.split("\n\n---\n\n")[0] == base
            else:
                budget = base.split("Your context budget is ")[1].split(";")[0]
                ok += s == swap_section(base, budget)
        hashes = {hashlib.sha256((first_system(r.run_id, day if arm == "econoview" else base_day) or "").encode()).hexdigest()[:12]
                  for r in runs if r.arm == arm}
        lines.append(f"- {label}: {ok}/{n} runs carry the expected system prompt "
                     f"({'CLM original' if arm == 'raw' else 'CLM original + SKILL text' if arm == 'econo12' else 'CLM with only the section swapped'}); "
                     f"distinct prompt hashes: {', '.join(sorted(hashes))}")
    return lines


def report(day: Path, baseline: Path | None = None) -> str:
    rs = view_runs(day)
    n = len(rs)
    edited = sum(r["model_edits"] > 0 for r in rs)
    passed = sum(r["passed"] for r in rs)
    crashes = [r["task"] for r in rs if r["exception"]]
    hook = sum(r["hook_errors"] or 0 for r in rs)
    det_ok, det_n = sum(r["det"][0] for r in rs), sum(r["det"][1] for r in rs)
    pos = [p for r in rs for p in r["positions"]]
    L = ["# EconoCLM-View report", "",
         "## Gate checks (REPORT.md deviation 9)", "",
         f"1. No crashes or hook errors: {'PASS' if not crashes and not hook else 'FAIL'} "
         f"(trials with an exception: {', '.join(crashes) or 'none'}; hook errors: {hook})",
         f"2. Model edits VIEW.md in at least 5 of 10 runs: {'PASS' if edited >= 5 else 'FAIL'} ({edited} of {n})",
         f"3. At least 6 of 10 tasks pass: {'PASS' if passed >= 6 else 'FAIL'} ({passed} of {n})",
         f"4. Same view, same bytes: {'PASS' if det_n and det_ok == det_n else 'FAIL'} ({det_ok} of {det_n} logged views re-render to the logged sha256)",
         "", "## Diagnostics (not gates)", "",
         f"- Notes kept whole: receipts with ignored lines {sum(r['ignored_receipts'] for r in rs)} (must be 0); "
         f"longest note {max([r['note_lines'] for r in rs] or [0])} lines",
         f"- Edits that rewrote the view from line 1: {sum(r['from_line1'] for r in rs)} of {sum(r['model_edits'] for r in rs)}",
         f"- Lines restored: {sum(r['restored'] for r in rs)}",
         f"- econo get / search: {sum(r['econo_get'] for r in rs)} / {sum(r['econo_search'] for r in rs)}",
         "", "## System prompts", "", *system_prompt_check(day, baseline), "",
         "## Per run", "",
         "| Task | Pass | $ | Calls | Model edits | Rejected | Auto appends | Dropped | Restored | Notes | obs lines | econo get / search | Peak prompt | Stale flags / followed |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rs:
        L.append(f"| {r['task']} | {r['passed']} | {r['cost']:.3f} | {r['calls']} | {r['model_edits']} | {r['rejected']} | "
                 f"{r['appends']} | {r['dropped']} | {r['restored']} | {len(r['notes'])} | {len(r['obs_lines'])} | "
                 f"{r['econo_get']} / {r['econo_search']} | {r['peak_prompt']:,} | {r['stale']} / {r['stale_followed']} |")
    L += ["", f"Where in the view edits happened (first changed line as a share of the view, over {len(pos)} edits): "
          + (f"median {statistics.median(pos):.0%}, quartiles {', '.join(f'{q:.0%}' for q in statistics.quantiles(pos, n=4))}"
             if len(pos) >= 2 else ", ".join(f"{p:.0%}" for p in pos) or "no edits"), ""]
    L += ["## Three VIEW.md files from real runs", ""]
    picks = sorted((v for r in rs for v in r["versions"]), key=lambda v: -len(set(
        ln.split()[0] for ln in v.read_text().splitlines() if ln.strip())))[:3]
    for v in picks:
        L += [f"`{v.parent.parent.name}/{v.parent.name}/{v.name}`:", "```", v.read_text().rstrip(), "```", ""]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("day", type=Path)
    ap.add_argument("--baseline", type=Path, default=None,
                    help="runs folder with the CLM and EconoCLM-Tools runs (for the system-prompt check)")
    args = ap.parse_args()
    text = report(args.day, args.baseline)
    (args.day / "view_report.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
