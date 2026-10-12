"""Per-run cost table: prefix-reuse FLOPs of each run, from the gateway's call log.

Why it exists: the one report every experiment uses. It is given run folders, or label folders that
hold them (each with the runner's summary.json), or a calls.jsonl directly. For each run it prints
one row:
- task, arm, pass, calls (agent and summary), retries, repeats (duplicate computations: a prompt sent
  again identically, e.g. a client's resend after a timeout);
- ΣP, ΣR, ΣU, ΣG, hit share ΣR/ΣP;
- PFLOPs: linear, attention, total;
- the summary calls' PFLOPs and the CLM-convention variant;
- the total as served by Purdue: computed on Purdue's actual prompt ids, which include its front
  end's random key reordering. Informational, not for comparing arms;
- Jev input tokens and fallbacks;
- how many calls hit the output cap (completion_tokens >= the request's max_tokens). vLLM reports such a
  reply as tool_calls when it holds a tool call (step 10b), so the cap is detected from the counts;
- how many calls passed validation.

It then prints a per-task x arm summary: the mean and per-repeat values of pass, calls, ΣP, ΣU, ΣG
and PFLOPs (linear, attention, total), with overflows, retries and repeats. It also shows what the
context manager did:
- EconoContext's actions, from each run's decisions (decisions.jsonl; older runs' session.sqlite3): arrival full/slice/preview, evictions,
  compactions, and Jev's questions and fallbacks with their error types;
- CLM's context edits from its usage.json: model edits and forced rollbacks.
A run whose request was refused for exceeding the model's window is an overflow. Since step 10c a run
that ends at a limit (overflow, or budget_exceeded under a token budget) is graded as it stands, and
counts by its grade; an overflow that was never graded (tc9's) counts as a FAIL. The `end` column says
how each run ended (submitted, max_steps, budget_exceeded, overflow; for CLM, budget_exceeded or
call_cap), where the runner recorded it.

It writes report.csv (one row per run), calls.csv (one row per call) and arms.csv (one row per task
and arm). A call whose rebuilt prompt fails validation is listed, and the exit code is 2: review
those runs before using their numbers.
The tokenizer is the measured model's own, at the revision in measure/model_constants.json.

Run: .venv/bin/python -m measure.report runs/ta1 [runs/gateway/<run_id>/calls.jsonl ...] [--out runs/ta1]
"""

import argparse
import csv
import json
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from .flops import measure_log
from .models import MEASURED, load

ROOT = Path(__file__).resolve().parents[1]
ARM_ORDER = ["raw", "raw-32k", "base", "base-32k", "summary-32k", "econo+jev", "econo+prior", "clm-32k", "clm-full"]
COLUMNS = ["run_id", "task", "arm", "repeat", "pass", "end", "overflow", "capped", "cut_summaries", "peak_P", "calls", "summary_calls", "retries", "repeats", "P", "R", "U", "G",
           "hit_share",
           "PFLOPs_lin", "PFLOPs_attn", "PFLOPs", "PFLOPs_summary", "PFLOPs_clm_aux", "PFLOPs_as_served_by_purdue",
           "jev_input_tokens", "jev_fallbacks", "valid", "invalid"]


def runs(paths: list[str]) -> list[dict]:
    """Each run: its calls.jsonl and, when the runner made it, its summary.json."""
    found = []
    for p in map(Path, paths):
        if p.name == "calls.jsonl":
            found.append({"calls_log": p, "summary": {"run_id": p.parent.name}})
            continue
        for summary_path in sorted(p.rglob("summary.json")) if p.is_dir() else []:
            summary = json.loads(summary_path.read_text())
            if summary.get("skipped") or not summary.get("calls_log"):
                continue
            if not (ROOT / summary["calls_log"]).exists():     # the trial made no call (e.g. its environment never started)
                print(f"no calls logged for {summary['run_id']} ({summary.get('exception')}): nothing to measure")
                continue
            found.append({"calls_log": ROOT / summary["calls_log"], "summary": summary, "dir": summary_path.parent})
    return found


def decisions_log(agent_dir: Path | None) -> list[dict]:
    """EconoContext's decisions in one trial: decisions.jsonl (step 12a on), else an older run's
    session.sqlite3. Each has at least rule, action and answers (a dict, or None)."""
    if agent_dir is None:
        return []
    log = agent_dir / "decisions.jsonl"
    if log.exists():
        return [json.loads(line) for line in log.read_text().splitlines() if line]
    db = agent_dir / "session.sqlite3"
    if not db.exists():
        return []
    with sqlite3.connect(db) as con:
        return [{"rule": rule, "action": action, "answers": json.loads(answers) if answers else None}
                for rule, action, answers in con.execute("SELECT rule, action, answers FROM decisions ORDER BY id")]


def run_actions(run: dict) -> dict:
    """One run's overflow flag, its calls that hit the output cap, and what its context manager did:
    EconoContext (from its decisions) or CLM (from its usage.json). Read directly; measure/
    never imports the method."""
    acts = {"overflow": False, "capped": 0, "arrival": Counter(), "evictions": 0, "compactions": 0, "jev_asked": 0,
            "jev_fallbacks": 0, "jev_errors": Counter(), "clm_edits": None, "clm_rollbacks": None}
    for line in Path(run["calls_log"]).read_text().splitlines():
        row = json.loads(line)
        if row.get("status") == 400 and "ContextWindowExceeded" in json.dumps(row.get("response")):
            acts["overflow"] = True
        cap = (row.get("request") or {}).get("max_tokens")
        if row.get("call_no") and row.get("status") == 200 and cap:
            acts["capped"] += ((row.get("usage") or {}).get("completion_tokens") or 0) >= cap
    folder = run.get("dir")
    for d in decisions_log(next(folder.glob("harbor/*/agent"), None) if folder else None):
        rule, action = d["rule"], d["action"]
        if rule.startswith("4 "):
            acts["arrival"][action] += 1
        acts["evictions"] += rule.startswith("6 ") and action == "evict"
        acts["compactions"] += rule.startswith("7 ") and action == "compacted"
        source = (d["answers"] or {}).get("source")
        if source:
            acts["jev_asked"] += 1
            if source.startswith("prior ("):
                acts["jev_fallbacks"] += 1
                acts["jev_errors"][source[len("prior ("):-1]] += 1
    acts["compactions"] += len((run.get("summary") or {}).get("compactions") or [])     # the Summary baseline
    usage = next(folder.glob("harbor/*/agent/usage.json"), None) if folder else None
    if usage:
        u = json.loads(usage.read_text())
        acts["clm_edits"], acts["clm_rollbacks"] = u.get("n_ctx_syncs_real"), u.get("n_retry_on_limit")
    return acts


def outcome(reward, overflow: bool):
    """1 pass, 0 fail, None not graded. A graded run counts by its grade, however it ended (step 10c:
    a run that ends at a limit is graded as it stands); an overflow that was never graded is a FAIL."""
    return 1 if reward == 1.0 else 0 if reward is not None else (0 if overflow else None)


def arm_summary(table: list[dict], extras: dict, out: Path) -> None:
    """Per task x arm: means and per-repeat values, and what the context manager did."""
    groups = defaultdict(list)
    for r in table:
        groups[(r["task"], r["arm"])].append(r)
    order = {arm: i for i, arm in enumerate(ARM_ORDER)}
    rows = []
    for (task, arm), runs_ in sorted(groups.items(), key=lambda kv: (kv[0][0], order.get(kv[0][1], 99), kv[0][1])):
        runs_ = sorted(runs_, key=lambda r: r["repeat"] or 0)
        acts = [extras[r["run_id"]] for r in runs_]
        passes = [outcome(r["pass"], e["overflow"]) for r, e in zip(runs_, acts)]
        graded = [p for p in passes if p is not None]
        mean = lambda key: statistics.mean(r[key] for r in runs_)  # noqa: E731
        arrival = sum((e["arrival"] for e in acts), Counter())
        errors = sum((e["jev_errors"] for e in acts), Counter())
        rows.append({
            "task": task, "arm": arm, "n": len(runs_), "passes": sum(graded), "graded": len(graded),
            "pass_by_repeat": " ".join("-" if p is None else str(p) for p in passes),
            "calls_mean": round(mean("calls"), 1), "P_mean": round(mean("P")), "U_mean": round(mean("U")),
            "G_mean": round(mean("G")), "PFLOPs_lin_mean": round(mean("PFLOPs_lin"), 4),
            "PFLOPs_attn_mean": round(mean("PFLOPs_attn"), 4), "PFLOPs_mean": round(mean("PFLOPs"), 4),
            "PFLOPs_by_repeat": " ".join(f"{r['PFLOPs']:.3f}" for r in runs_),
            "overflows": sum(e["overflow"] for e in acts), "capped": sum(e["capped"] for e in acts),
            "peak_P": max(r["peak_P"] for r in runs_),
            "retries": sum(r["retries"] for r in runs_),
            "repeats": sum(r["repeats"] for r in runs_),
            "arrival_full_slice_preview": f"{arrival['full']}/{arrival['slice']}/{arrival['preview']}" if arrival else "",
            "evictions": sum(e["evictions"] for e in acts), "compactions": sum(e["compactions"] for e in acts),
            "jev_asked": sum(e["jev_asked"] for e in acts), "jev_fallbacks": sum(e["jev_fallbacks"] for e in acts),
            "jev_errors": "; ".join(f"{k} x{v}" for k, v in errors.items()),
            "clm_edits": sum(e["clm_edits"] or 0 for e in acts) if any(e["clm_edits"] is not None for e in acts) else "",
            "clm_rollbacks": sum(e["clm_rollbacks"] or 0 for e in acts) if any(e["clm_rollbacks"] is not None for e in acts) else ""})
    print(f"\n{'task':34s} {'arm':10s} {'pass':>5s} {'by rep':>7s} {'calls':>6s} {'ΣP':>9s} {'ΣU':>7s} {'ΣG':>7s} "
          f"{'PFLOPs':>7s} {'by repeat':>20s} {'ovf':>3s} {'cap':>3s} {'ret':>3s} {'rep':>3s} {'peak P':>7s}  context manager")
    for r in rows:
        if r["arm"].startswith("econo"):
            manager = (f"arrival f/s/p {r['arrival_full_slice_preview'] or '0/0/0'}, evict {r['evictions']}, compact "
                       f"{r['compactions']}, Jev {r['jev_asked']} asked {r['jev_fallbacks']} fallbacks"
                       + (f" ({r['jev_errors']})" if r["jev_errors"] else ""))
        elif r["arm"].startswith("summary"):
            manager = f"compactions {r['compactions']}"
        elif r["arm"].startswith("clm"):
            manager = f"edits {r['clm_edits']}, rollbacks {r['clm_rollbacks']}"
        else:
            manager = ""
        print(f"{r['task'][:34]:34s} {r['arm']:10s} {r['passes']:>2d}/{r['graded']:<2d} {r['pass_by_repeat']:>7s} "
              f"{r['calls_mean']:6.1f} {r['P_mean']:9,d} {r['U_mean']:7,d} {r['G_mean']:7,d} {r['PFLOPs_mean']:7.3f} "
              f"{r['PFLOPs_by_repeat']:>20s} {r['overflows']:3d} {r['capped']:3d} {r['retries']:3d} {r['repeats']:3d} {r['peak_P']:7,d}  {manager}")
    if rows:
        with (out / "arms.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("paths", nargs="+")
    p.add_argument("--out", help="folder for report.csv and calls.csv (default: the first path's folder)")
    a = p.parse_args()
    from transformers import AutoTokenizer
    model = load(MEASURED)
    tokenizer = AutoTokenizer.from_pretrained(MEASURED, revision=model["revision"])
    out = Path(a.out or (a.paths[0] if Path(a.paths[0]).is_dir() else Path(a.paths[0]).parent))
    table, per_call, invalid, extras = [], [], [], {}
    for run in runs(a.paths):
        result = measure_log(run["calls_log"], tokenizer, model["C_token"], model["C_attn"])
        s, t = run["summary"], result["totals"]
        run_id = s["run_id"]
        parts = run_id.split(".")
        owner = s.get("owner") or {}
        extras[run_id] = acts = run_actions(run)
        repeat = parts[3][1:] if len(parts) == 4 and parts[3][1:].isdigit() else None
        table.append({"run_id": run_id, "task": parts[2] if len(parts) == 4 else "", "arm": parts[1] if len(parts) == 4 else "",
                      "repeat": int(repeat) if repeat else None, "pass": s.get("reward"),
                      "end": s.get("end") or ("overflow" if acts["overflow"] else None), "overflow": acts["overflow"],
                      "capped": acts["capped"], "cut_summaries": s.get("cut_summaries"),
                      "peak_P": max((row["P"] for row in result["rows"]), default=0),
                      "calls": t["calls"], "summary_calls": t["summary_calls"],
                      "retries": t["retries"], "repeats": t["repeats"], "P": t["P"], "R": t["R"], "U": t["U"], "G": t["G"],
                      "hit_share": round(t["hit_share"], 4), "PFLOPs_lin": t["F_lin"] / 1e15,
                      "PFLOPs_attn": t["F_attn"] / 1e15, "PFLOPs": t["F"] / 1e15, "PFLOPs_summary": t["F_summary"] / 1e15,
                      "PFLOPs_clm_aux": t["F_clm_aux"] / 1e15,
                      "PFLOPs_as_served_by_purdue": t["F_server"] / 1e15 if t["F_server"] is not None else None,
                      "jev_input_tokens": (owner.get("jev") or {}).get("input_tokens"),
                      "jev_fallbacks": (s.get("jev_fallbacks") or {}).get("fell_back"),
                      "valid": t["valid"], "invalid": t["invalid"]})
        checks = {c["call_no"]: c for c in result["validation"]}
        for row in result["rows"]:
            check = checks[row["call_no"]]
            per_call.append({"run_id": run_id, **row, "dP": check["dP"], "validation": check["note"]})
        invalid += [(run_id, c) for c in result["validation"] if not c["ok"]]
    print(f"{'run':44s} {'pass':>4s} {'calls':>7s} {'retry':>5s} {'rep':>3s} {'ΣP':>9s} {'ΣR':>9s} {'ΣG':>7s} {'hit':>5s} "
          f"{'PF lin':>7s} {'PF attn':>7s} {'PFLOPs':>7s} {'PF summ':>7s} {'PF CLM':>7s} {'PF Pur*':>7s} {'Jev':>7s} {'fb':>3s} {'ok':>5s}")
    for r in table:
        server = f"{r['PFLOPs_as_served_by_purdue']:7.3f}" if r["PFLOPs_as_served_by_purdue"] is not None else f"{'-':>7s}"
        print(f"{r['run_id'][:44]:44s} {str(r['pass']):>4s} {r['calls']:4d}/{r['summary_calls']:<2d} {r['retries']:5d} {r['repeats']:3d} "
              f"{r['P']:9,d} {r['R']:9,d} {r['G']:7,d} {r['hit_share']:5.3f} {r['PFLOPs_lin']:7.3f} {r['PFLOPs_attn']:7.3f} "
              f"{r['PFLOPs']:7.3f} {r['PFLOPs_summary']:7.3f} {r['PFLOPs_clm_aux']:7.3f} {server} "
              f"{str(r['jev_input_tokens'] or '-'):>7s} {str(r['jev_fallbacks'] if r['jev_fallbacks'] is not None else '-'):>3s} "
              f"{r['valid']:2d}/{r['valid'] + r['invalid']:<2d}")
    print("PF summ: the summary calls' share of PFLOPs. PF CLM: summary calls fully prefilled, outside the cache "
          "(CLM's aux convention).\n* PF Pur: as served by Purdue (includes front-end key reordering); informational, "
          "not for comparing arms.")
    out.mkdir(parents=True, exist_ok=True)
    with (out / "report.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(table)
    if per_call:
        with (out / "calls.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(per_call[0]))
            w.writeheader()
            w.writerows(per_call)
    arm_summary(table, extras, out)
    print(f"wrote {out / 'report.csv'}, {out / 'calls.csv'} and {out / 'arms.csv'}")
    for run_id, check in invalid:
        print(f"INVALID {run_id} call {check['call_no']}: {check['note']}")
    sys.exit(2 if invalid else 0)


if __name__ == "__main__":
    main()
