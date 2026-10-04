"""The results table: Raw CLM vs EconoCLM, per arm and per task.

  python -m econoclm.analysis.results_table runs/<date> [--arms raw]

Writes runs/<date>/results.md and results.csv (one row per run; the .md also has
per-arm sums). Sources: Harbor result.json (reward, wall time), the gateway
ledger (tokens, $, calls, latency, rate limits), CLM's usage.json / timing.json /
context_snapshots (edits, rollbacks, peak context, commands), econo.sqlite
(EconoCLM's DB use, stale flags, hook errors, the text we added).

infra_fail marks a trial that ended in an exception while the gateway saw a
rate-limit or upstream failure as its last reply: not the agent's fault, and
reported apart instead of being counted as a plain task failure.
"""

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from ..quote.messages import default_count
from .common import Run, load_runs

GET = re.compile(r"^\s*(\d+)\b")
READ_OPS = {"list", "get", "search", "sql_read"}


def tokens(text: str) -> int:
    return default_count([{"role": "user", "content": text}])


def commands(run: Run) -> list[str]:
    d = run.agent_dir()
    p = d / "timing.json" if d else None
    if not p or not p.exists():
        return []
    return [e["cmd"] for e in json.loads(p.read_text()) if "cmd" in e and "bash_s" in e]


def peak_context(run: Run) -> int | None:
    d = run.agent_dir()
    if not d or not (d / "context_snapshots").is_dir():
        return None
    peaks = [json.loads(p.read_text()).get("tokens") or 0
             for p in (d / "context_snapshots").glob("turn-*.json")]
    return max(peaks) if peaks else None


def econo_metrics(run: Run) -> dict:
    store = run.store()
    if store is None:
        return {}
    ops = store.rows("SELECT * FROM econo_ops")
    obs = {r["obs_id"]: r for r in store.rows("SELECT * FROM observations")}
    status = store.rows("SELECT * FROM status_lines ORDER BY turn")
    edits = store.rows("SELECT * FROM edits")
    files = store.rows("SELECT * FROM files")
    gets = [int(m.group(1)) for o in ops if o["op"] == "get"
            for m in [GET.match(o["args"] or "")] if m]
    # Stale flags: each (turn, path) first shown; followed by a re-read of that path?
    flags, seen = [], set()
    for s in status:
        for p in json.loads(s["stale_paths"] or "[]"):
            if p not in seen:
                seen.add(p)
                flags.append((s["turn"], p))
    reread = sum(any(f["path"] == p and f["turn_read"] > t for f in files) for t, p in flags)
    added = sum(tokens(s["text"]) for s in status) + sum(tokens(e["text"] or "") for e in edits) \
        + sum(tokens(f"[obs {i}]" + (f" (cut in context; full: econo get {i})"
                                     if o["cut_in_context"] else "")) for i, o in obs.items())
    n_turns = max(len(status), 1)
    return {
        "db_reads": sum(o["op"] in READ_OPS for o in ops),
        "db_writes": sum(o["op"] == "sql_write" for o in ops),
        "econo_get": len(gets), "econo_search": sum(o["op"] == "search" for o in ops),
        "econo_sql": sum(o["op"].startswith("sql") for o in ops),
        "econo_get_on_cut": sum(1 for n in gets if obs.get(n, {}).get("cut_in_context")),
        "cut_outputs": sum(o["cut_in_context"] for o in obs.values()),
        "saved_outputs": len(obs),
        "stale_flags": len(flags), "stale_flags_reread": reread,
        "hook_errors": store.rows("SELECT COUNT(*) n FROM hook_errors")[0]["n"],
        "avg_added_tokens_per_turn": round(added / n_turns, 1),
        "quotes": len(edits),
    }


# finish_reason values after which the call is resent with the same prompt below CLM's
# step counter: the gateway sees one more call than CLM counts.
RETRY_REASONS = {"malformed_function_call"}


def row_for(run: Run) -> dict:
    c = run.calls
    s = lambda key: sum((r[key] or 0) for r in c)  # noqa: E731
    wait_ms = sum((r["ratelimit_wait_ms"] or 0) + (r["queue_ms"] or 0) for r in run.all_rows)
    cmds = commands(run)
    last = run.all_rows[-1] if run.all_rows else None
    infra = bool(run.exception) and last is not None and last["http_status"] in (429, 502, 503)
    wall = run.wall_s
    row = {
        "run_id": run.run_id, "arm": run.arm, "task": run.task, "rep": run.rep,
        "reward": run.reward, "passed": int(run.reward == 1),
        "exception": run.exception or "", "infra_fail": int(infra),
        "cost_usd": round(sum(r["cost_usd"] or 0 for r in run.all_rows), 6),
        "input_tokens": s("prompt_tokens"), "cached_input": s("cached_tokens"),
        "uncached_input": s("uncached_tokens"), "output_tokens": s("output_tokens"),
        "reasoning_tokens": s("reasoning_tokens"), "model_calls": len(c),
        "clm_lm_calls": run.usage.get("n_lm_calls"),
        "finish_length": sum(r["finish_reason"] == "length" for r in c),
        "usage_anomalies": sum(bool(r.get("usage_anomaly")) for r in run.all_rows),
        "provider_retries": sum(r["finish_reason"] in RETRY_REASONS for r in c),
        "context_edits": run.usage.get("n_ctx_syncs"),
        "context_edits_real": run.usage.get("n_ctx_syncs_real"),
        "edits_rejected": run.usage.get("n_ctx_rejected"),
        "peak_context": peak_context(run),
        "rollbacks": run.usage.get("n_retry_on_limit"),
        "turns_rolled_back": run.usage.get("n_turns_rolled_back"),
        "wall_s": round(wall, 1) if wall is not None else None,
        "wall_s_net_of_ratelimit": round(wall - wait_ms / 1000, 1) if wall is not None else None,
        "mean_latency_ms": round(s("latency_ms") / len(c), 1) if c else None,
        "upstream_retries": sum((r["upstream_attempts"] or 1) - 1 for r in run.all_rows),
        "ratelimit_wait_s": round(wait_ms / 1000, 1),
        "commands": len(cmds),
        "repeated_commands": sum(n - 1 for n in Counter(cmds).values() if n > 1),
    }
    row.update(econo_metrics(run))
    return row


SUM_KEYS = ["passed", "infra_fail", "cost_usd", "input_tokens", "cached_input", "uncached_input",
            "output_tokens", "model_calls", "finish_length", "usage_anomalies", "provider_retries", "context_edits", "rollbacks",
            "upstream_retries", "ratelimit_wait_s", "commands", "repeated_commands", "db_reads",
            "db_writes", "econo_get", "econo_get_on_cut", "cut_outputs", "stale_flags",
            "stale_flags_reread", "hook_errors"]


def arm_summary(rows: list[dict]) -> dict[str, dict]:
    out = defaultdict(lambda: defaultdict(float))
    for r in rows:
        a = out[r["arm"]]
        a["runs"] += 1
        for k in SUM_KEYS:
            if isinstance(r.get(k), (int, float)):
                a[k] += r[k]
        for k in ("wall_s", "wall_s_net_of_ratelimit", "peak_context", "mean_latency_ms",
                  "avg_added_tokens_per_turn"):
            if isinstance(r.get(k), (int, float)):
                a[f"_{k}_sum"] += r[k]
                a[f"_{k}_n"] += 1
    for a in out.values():
        a["usd_per_solved"] = a["cost_usd"] / a["passed"] if a["passed"] else None
        for k in ("wall_s", "wall_s_net_of_ratelimit", "peak_context", "mean_latency_ms",
                  "avg_added_tokens_per_turn"):
            n = a.pop(f"_{k}_n", 0)
            total = a.pop(f"_{k}_sum", 0)
            a[f"mean_{k}"] = total / n if n else None
    return {k: dict(v) for k, v in out.items()}


def fmt(v) -> str:
    if v is None:
        return "–"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, float):
        return f"{v:.4f}" if abs(v) < 10 else f"{v:,.1f}"
    return f"{v:,}" if isinstance(v, int) else str(v)


METRICS = [("Runs", "runs"), ("Tasks passed", "passed"), ("Infra failures (rate limit)", "infra_fail"),
           ("Total $", "cost_usd"), ("$ / solved task", "usd_per_solved"),
           ("Input tokens", "input_tokens"), ("Cached input", "cached_input"),
           ("Uncached input", "uncached_input"), ("Output + thinking tokens", "output_tokens"),
           ("Model calls", "model_calls"), ("Calls cut by length", "finish_length"),
           ("Usage anomalies (ledger)", "usage_anomalies"),
           ("Provider-side retries (malformed_function_call)", "provider_retries"),
           ("Context edits", "context_edits"), ("Mean peak context (tokens)", "mean_peak_context"),
           ("Rollbacks / overflow retries", "rollbacks"), ("Mean wall time / task (s)", "mean_wall_s"),
           ("Mean wall time net of rate-limit waits (s)", "mean_wall_s_net_of_ratelimit"),
           ("Mean latency / call (ms)", "mean_mean_latency_ms"),
           ("Upstream retries (429/503)", "upstream_retries"), ("Rate-limit wait (s)", "ratelimit_wait_s"),
           ("Commands", "commands"), ("Repeated identical commands", "repeated_commands"),
           ("DB reads", "db_reads"), ("DB writes", "db_writes"), ("econo get", "econo_get"),
           ("econo get on cut outputs", "econo_get_on_cut"), ("Cut outputs", "cut_outputs"),
           ("Stale flags shown", "stale_flags"), ("…followed by a re-read", "stale_flags_reread"),
           ("Hook errors", "hook_errors"),
           ("Avg tokens our text added / turn", "mean_avg_added_tokens_per_turn")]

TASK_COLS = ["run_id", "passed", "exception", "infra_fail", "cost_usd", "model_calls", "clm_lm_calls",
             "finish_length", "cached_input",
             "uncached_input", "output_tokens", "context_edits", "rollbacks", "peak_context",
             "wall_s", "ratelimit_wait_s", "repeated_commands", "econo_get", "stale_flags",
             "hook_errors"]


def markdown(rows: list[dict]) -> str:
    summ = arm_summary(rows)
    arms = sorted(summ, key=lambda a: (a != "raw", a))
    lines = ["# Results", "", "| Metric | " + " | ".join(arms) + " |",
             "|---|" + "---|" * len(arms)]
    for label, key in METRICS:
        lines.append(f"| {label} | " + " | ".join(fmt(summ[a].get(key)) for a in arms) + " |")
    lines += ["", "## Per run", "", "| " + " | ".join(TASK_COLS) + " |",
              "|" + "---|" * len(TASK_COLS)]
    for r in sorted(rows, key=lambda r: (r["task"], r["arm"] != "raw", r["rep"])):
        lines.append("| " + " | ".join(fmt(r.get(c)) for c in TASK_COLS) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("day", type=Path)
    ap.add_argument("--arms", default=None)
    args = ap.parse_args()
    arms = set(args.arms.split(",")) if args.arms else None
    rows = [row_for(r) for r in load_runs(args.day, arms)]
    keys = sorted({k for r in rows for k in r}, key=lambda k: (k not in TASK_COLS, k))
    with open(args.day / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    text = markdown(rows)
    (args.day / "results.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
