"""Independent checks of every number in the full-control (Gemini) experiment.

For each trial under the given labels it recomputes, from raw sources, what the runner and the
compare script report, and flags any mismatch:
  cost      every gateway call re-priced from its raw usage JSON with the Gemini card
  calls     gateway calls vs the agent's own call log (trajectory.json; includes summary calls, retries)
  tokens    prompt / cached / output totals: trajectory usage vs gateway
  reward    Harbor's result.json vs the grader's verifier/reward.txt
  summary   summary.json run_cost_usd vs the gateway sum
  jev       Jev input tokens: session database vs summary.json
  errors    any trial exception
Then it rebuilds per-arm totals and the paired statistics from scratch, and exports every gateway
row for these runs to CSV. Writes a report; exit code 1 if any check fails.

Run: .venv/bin/python benchmarks/tblite/verify_gemini.py --label gx1 --label gx1b --label gx23 [--out runs/.../VERIFY.md]
"""

import argparse
import csv
import json
import math
import sqlite3
import statistics as st
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "econocontext.sqlite3"
CARD = yaml.safe_load((ROOT / "config" / "billing_rates.yaml").read_text())["vertex_gemini"]["gemini-3.6-flash"]
RATES = CARD["periods"][0]["tiers"][0]   # valid until 2026-12-31 (all runs are 2026-10)


def usage_parts(u: dict) -> tuple[int, int, int, int]:
    """(prompt, cached, completion, reasoning) from an OpenAI-compatible usage dict (Vertex Gemini)."""
    prompt = int(u.get("prompt_tokens") or 0)
    cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    completion = int(u.get("completion_tokens") or 0)
    reasoning = int((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
    return prompt, cached, completion, reasoning


def price(u: dict) -> tuple[float, float]:
    """Cost with reasoning billed as output, two ways: reasoning outside completion_tokens (Vertex,
    EconoCLM finding: total = prompt + completion + reasoning) and inside it. Returns both."""
    prompt, cached, completion, reasoning = usage_parts(u)
    base = ((prompt - cached) * RATES["input_per_mtok"] + cached * RATES["cache_read_per_mtok"]) / 1e6
    outside = base + (completion + reasoning) * RATES["output_per_mtok"] / 1e6
    inside = base + max(completion, reasoning) * RATES["output_per_mtok"] / 1e6
    return outside, inside


def betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularized incomplete beta (Numerical Recipes, betacf)."""
    qab, qap, qam, c, d = a + b, a + 1, a - 1, 1.0, 1 - (a + b) * x / (a + 1)
    d = 1 / (d if abs(d) > 1e-30 else 1e-30)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((qam + m2) * (a + m2)), -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1 + aa * d
            d = 1 / (d if abs(d) > 1e-30 else 1e-30)
            c = 1 + aa / c if abs(1 + aa / c) > 1e-30 else 1e-30
            h *= d * c
        if abs(d * c - 1) < 1e-12:
            break
    return h


def t_pvalue(t: float, df: int) -> float:
    """Two-sided p-value of Student's t: I_{df/(df+t^2)}(df/2, 1/2)."""
    x = df / (df + t * t)
    a, b = df / 2, 0.5
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    return front * betacf(a, b, x) / a if x < (a + 1) / (a + b + 2) else 1 - front * betacf(b, a, 1 - x) / b


def sign_pvalue(k: int, n: int) -> float:
    """Exact two-sided sign test: P(at least this lopsided) under 50/50."""
    k = max(k, n - k)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n)


def trials(labels):
    for label in labels:
        for summary in sorted((ROOT / "runs" / label).glob("*/*/summary.json")):
            yield label, summary


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--label", action="append", required=True)
    p.add_argument("--out", default=str(ROOT / "runs" / "2026-10-06-gemini" / "VERIFY.md"))
    a = p.parse_args()
    db = sqlite3.connect(DB)
    lines, failures, rows = [], [], []
    for label, summary_path in trials(a.label):
        s = json.loads(summary_path.read_text())
        run, out = s["run"], summary_path.parent
        if s.get("skipped"):
            lines.append(f"- {run}: SKIPPED ({s['skipped']})")
            continue
        trial = next((out / "harbor").glob("*"), None)
        traj = json.loads((trial / "agent" / "trajectory.json").read_text()) if trial else {}
        result = json.loads((trial / "result.json").read_text()) if trial and (trial / "result.json").exists() else {}
        gw = db.execute("SELECT raw, cost_usd, cost_complete, uncached_input, cache_read, output FROM outcomes "
                        "WHERE run_id=? ORDER BY created_at", (run,)).fetchall()
        checks = {}
        # cost: re-price every call from its raw usage
        outside = inside = 0.0
        incomplete = 0
        for raw, cost, complete, *_ in gw:
            u = json.loads(raw) if raw else {}
            o, i = price(u)
            outside, inside = outside + o, inside + i
            incomplete += 0 if complete else 1
        stored = sum(r[1] or 0 for r in gw)
        checks["cost"] = abs(stored - outside) < 1e-6
        checks["cost_complete"] = incomplete == 0
        # calls: gateway vs the agent's own log
        agent_calls = len(traj.get("calls") or [])
        checks["calls"] = len(gw) == agent_calls
        # tokens: trajectory usage vs gateway
        tp = tc = to = 0
        for c in traj.get("calls") or []:
            pr, ca, co, re_ = usage_parts(c.get("usage") or {})
            tp, tc, to = tp + pr, tc + ca, to + co + re_
        gp = sum((r[3] or 0) + (r[4] or 0) for r in gw)
        gc = sum(r[4] or 0 for r in gw)
        go = sum(r[5] or 0 for r in gw)
        checks["tokens"] = (tp, tc, to) == (gp, gc, go)
        # reward: Harbor vs the grader's file
        reward = ((result.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        reward_file = trial / "verifier" / "reward.txt" if trial else None
        file_reward = float(reward_file.read_text().strip()) if reward_file and reward_file.exists() else None
        checks["reward"] = reward == file_reward
        checks["summary"] = abs((s.get("run_cost_usd") or 0) - stored) < 1e-4 and s.get("reward") == reward
        # Jev tokens: session database vs summary
        session = trial / "agent" / "session.sqlite3" if trial else None
        jev_db = 0
        if session and session.exists():
            with sqlite3.connect(session) as sdb:
                for (usage,) in sdb.execute("SELECT jev_usage FROM decisions WHERE jev_usage IS NOT NULL"):
                    jev_db += json.loads(usage).get("input_tokens", 0)
        jev_summary = ((s.get("owner") or {}).get("jev") or {}).get("input_tokens", 0)
        checks["jev"] = jev_db == jev_summary
        exception = (result.get("exception_info") or {}).get("exception_type")
        bad = [k for k, ok in checks.items() if not ok]
        if bad or exception:
            failures.append(run)
        lines.append(f"- {run}: cost ${stored:.6f} (re-priced ${outside:.6f}; reasoning-inside ${inside:.6f}), "
                     f"calls {len(gw)}/{agent_calls}, reward {reward} (file {file_reward}), jev {jev_db}"
                     + (f", EXCEPTION {exception}" if exception else "") + (f"  FAIL: {bad}" if bad else "  ok"))
        task, rep = out.name.rsplit("-r", 1)
        rows.append({"label": label, "arm": out.parent.name, "task": task, "repeat": int(rep), "cost": stored,
                     "reward": reward, "exception": exception, "jev": jev_db})
    # export every gateway row for these runs
    export = Path(a.out).with_name("gateway_outcomes.csv")
    runs = [json.loads(sp.read_text())["run"] for _, sp in trials(a.label)]
    with export.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "created_at", "uncached_input", "cache_read", "output", "reasoning", "cost_usd",
                    "cost_complete", "raw_usage"])
        for run in runs:
            for r in db.execute("SELECT run_id, created_at, uncached_input, cache_read, output, reasoning, cost_usd, "
                                "cost_complete, raw FROM outcomes WHERE run_id=? ORDER BY created_at", (run,)):
                w.writerow(r)
    # totals and paired statistics, rebuilt from the rows above (a crashed trial is replaced by a later label's)
    # Only trials that ran to the grader count. A crashed or aborted trial (an exception, or no
    # reward) is listed above but kept out of totals and pairs; a later label's trial replaces it.
    cell, aborted = {}, []
    for r in rows:
        key = (r["arm"], r["task"], r["repeat"])
        if r["exception"] or r["reward"] is None:
            aborted.append(r)
            continue
        cell[key] = r
    stats = ["", "## Totals (rebuilt from raw rows; valid trials only)", "",
             f"Kept out (crashed or aborted, not graded): {len(aborted)} -- "
             + ", ".join(f"{r['label']} {r['arm']} {r['task']} r{r['repeat']} ({r['exception']})" for r in aborted), ""]
    arms = sorted({k[0] for k in cell}, key=lambda x: (x != "raw", x))
    for arm in arms:
        rs = [r for k, r in cell.items() if k[0] == arm]
        stats.append(f"- {arm}: {len(rs)} trials, passed {sum(r['reward'] == 1.0 for r in rs)}, "
                     f"cost ${sum(r['cost'] for r in rs):.4f}, Jev tokens {sum(r['jev'] for r in rs):,}, "
                     f"exceptions {sum(1 for r in rs if r['exception'])}")
    for arm in arms[1:]:
        pairs = [(cell[("raw", t, n)]["cost"], r["cost"]) for (a_, t, n), r in cell.items()
                 if a_ == arm and ("raw", t, n) in cell]
        if len(pairs) > 2:
            d = [e - r for r, e in pairs]
            t = st.mean(d) / (st.stdev(d) / math.sqrt(len(d)))
            stats.append(f"- {arm} vs raw, {len(pairs)} same-repeat pairs: raw ${sum(r for r, _ in pairs):.4f}, "
                         f"{arm} ${sum(e for _, e in pairs):.4f} ({100 * sum(d) / sum(r for r, _ in pairs):+.1f}%), "
                         f"mean diff ${st.mean(d):.4f}, sd ${st.stdev(d):.4f}, paired t {t:.2f} (df {len(d) - 1}), "
                         f"cheaper in {sum(x < 0 for x in d)}/{len(d)}")
            stats.append(f"  two-sided p: paired t {t_pvalue(t, len(d) - 1):.3f}; exact sign test "
                         f"{sign_pvalue(sum(x < 0 for x in d), len(d)):.3f} (no normality assumption)")
    report = [f"# Verification of {', '.join(a.label)}", "",
              f"Checks per trial: cost re-priced from raw usage (Gemini card: input ${RATES['input_per_mtok']}/M, "
              f"cached ${RATES['cache_read_per_mtok']}/M, output ${RATES['output_per_mtok']}/M incl. reasoning), "
              "gateway calls = agent calls, token totals match, reward = grader file, summary = gateway, "
              "Jev tokens session = summary.", "",
              f"**Result: {len(failures)} trial(s) with a failed check or an exception.**", ""] + lines + stats + \
             ["", f"Gateway rows exported to {export.name}."]
    Path(a.out).write_text("\n".join(report) + "\n")
    print("\n".join(report))
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
