"""PASS/FAIL checks for the pilot gates, read from a runs folder. Read-only.

  python -m econoclm.analysis.gate_check runs/<phase> --gate 3     # Raw CLM pilot
  python -m econoclm.analysis.gate_check runs/<phase> --gate 5     # EconoCLM pilot

Gate 3, per raw run:
  - gateway calls (HTTP 200 rows) = LM calls in CLM's usage.json + provider-side retries
    (one extra row per reply with a known retry reason, RETRY_REASONS; any other extra
    row fails)
  - a Harbor reward exists
  - no call ended with finish_reason = length (else: the one allowed fallback,
    max_tokens 8192 in BOTH configs, then rerun the pilot)
  - usage anomalies = 0 (ledger usage_anomaly: usage numbers that don't add up, or a
    200 reply with no output count; see core/usage.py)
Gate 5, per econo run: the same four, plus
  - saved outputs = commands run (CLM's timing.json)
  - `econo get` in the sandbox was byte-identical to the host copy (sha1, up to 3
    random IDs, checked at the end of the run: table get_checks)
  - hook errors = 0
  - quote_check runs
and it prints how the model used `econo`, and 3 example [econo] lines.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from . import quote_check
from .common import RETRY_REASONS, Run, load_runs
from .results_table import commands


def base_checks(run: Run) -> list[tuple[str, bool, str]]:
    lm_calls = run.usage.get("n_lm_calls")
    retries = sum(r["finish_reason"] in RETRY_REASONS for r in run.calls)
    length = sum(r["finish_reason"] == "length" for r in run.calls)
    anomalies = sum(bool(r.get("usage_anomaly")) for r in run.all_rows)
    return [
        ("gateway calls = CLM LM calls + provider-side retries",
         lm_calls is not None and len(run.calls) == lm_calls + retries,
         f"gateway {len(run.calls)} (all rows {len(run.all_rows)}), CLM {lm_calls}, "
         f"provider-side retries {retries}"),
        ("Harbor reward exists", run.reward is not None,
         f"reward {run.reward}" + (f", exception {run.exception}" if run.exception else "")),
        ("no finish_reason = length", length == 0, f"{length} call(s) cut by length"),
        ("usage anomalies = 0", anomalies == 0, f"{anomalies} ledger row(s) with usage_anomaly"),
    ]


def econo_checks(run: Run) -> list[tuple[str, bool, str]]:
    store = run.store()
    if store is None:
        return [("econo.sqlite exists", False, f"missing in {run.dir}")]
    n_obs = store.rows("SELECT COUNT(*) n FROM observations")[0]["n"]
    n_cmd = len(commands(run))
    gets = store.rows("SELECT * FROM get_checks")
    errors = store.rows("SELECT COUNT(*) n FROM hook_errors")[0]["n"]
    try:
        quote_check.summary(quote_check.check([run]))
        qc_ok, qc_note = True, "ran"
    except Exception as exc:  # noqa: BLE001 - reported as a failed check
        qc_ok, qc_note = False, f"{type(exc).__name__}: {exc}"
    return [
        ("saved outputs = commands run", n_obs == n_cmd, f"saved {n_obs}, commands {n_cmd}"),
        ("econo get byte-identical in sandbox", bool(gets) and all(g["match"] for g in gets),
         f"{sum(g['match'] for g in gets)}/{len(gets)} IDs match "
         f"({', '.join(str(g['obs_id']) for g in gets)})"),
        ("hook errors = 0", errors == 0, f"{errors} hook error(s)"),
        ("quote_check runs", qc_ok, qc_note),
    ]


def econo_usage(run: Run) -> list[str]:
    store = run.store()
    if store is None:
        return []
    ops = Counter(o["op"] for o in store.rows("SELECT op FROM econo_ops"))
    status = store.rows("SELECT text FROM status_lines ORDER BY turn")
    quotes = store.rows("SELECT text FROM edits ORDER BY turn")
    examples = ([status[0]["text"]] if status else []) + \
               ([quotes[0]["text"]] if quotes else []) + \
               ([status[-1]["text"]] if len(status) > 1 else [])
    return [f"econo use by the model: {dict(ops) or 'none'} (total {sum(ops.values())})",
            "example [econo] lines:"] + [f"  {t}" for t in examples[:3]]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("day", type=Path)
    ap.add_argument("--gate", type=int, choices=(3, 5), required=True)
    args = ap.parse_args(argv)
    arm = "raw" if args.gate == 3 else "econo"
    runs = load_runs(args.day, {arm})
    if not runs:
        print(f"no {arm} runs in {args.day}")
        return 1
    ok = True
    for run in runs:
        checks = base_checks(run) + (econo_checks(run) if arm == "econo" else [])
        print(f"== {run.run_id}")
        for name, passed, note in checks:
            ok &= passed
            print(f"  [{'PASS' if passed else 'FAIL'}] {name}: {note}")
        if not checks[2][1]:
            print("  -> allowed fallback: set max_tokens: 8192 in BOTH arm configs, rerun the pilot")
        for line in econo_usage(run) if arm == "econo" else []:
            print(f"  {line}")
    print(f"GATE {args.gate}: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
