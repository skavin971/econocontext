"""PFLOP accuracy: run CLM's agent (zero-shot, as released) on BrowseComp-Plus questions (pflop_accuracy/README.md).

Only CLM runs here: no EconoContext, no other baseline. It uses the same runner, settings and search service as our
BrowseComp-Plus pilot (benchmarks/bcp/run.py: budget 23,560 tokens, 100 turns, 4,096 output tokens, BM25 top 10 with
512-token snippets), under its own label, so these runs never mix with any other experiment.

Use:  .venv/bin/python pflop_accuracy/run_clm.py --backend purdue            (Path A, 8 questions)
      .venv/bin/python pflop_accuracy/run_clm.py --backend vllm              (Path B, 8 questions)
      --questions N   fewer questions (a first try: --questions 1)
      --judge         also grade the answers (Path A only: the judge model is on Purdue)
Each question takes 5-40 minutes (CLM's runs on BrowseComp-Plus are long). One run at a time: keep it that way on vLLM,
so no other traffic shares or evicts the prefix cache being measured.
"""

import argparse
import time

from common import GATEWAY, LABEL_PREFIX, PY, QUESTIONS, RUNS, get_json, run


def main() -> None:
    ap = argparse.ArgumentParser(description="PFLOP accuracy: run CLM on BrowseComp-Plus questions")
    ap.add_argument("--backend", required=True, choices=("purdue", "vllm"))
    ap.add_argument("--questions", type=int, default=QUESTIONS, help=f"how many pilot questions (default {QUESTIONS})")
    ap.add_argument("--label", help=f"default: {LABEL_PREFIX}-<backend>-<YYYYMMDD>")
    ap.add_argument("--judge", action="store_true", help="grade the answers too (Path A only)")
    a = ap.parse_args()
    label = a.label or f"{LABEL_PREFIX}-{a.backend}-{time.strftime('%Y%m%d')}"
    if not label.startswith(LABEL_PREFIX):
        raise SystemExit(f"labels for this step start with {LABEL_PREFIX!r}")
    if (RUNS / label).exists():
        raise SystemExit(f"{RUNS / label} already exists: pass a new --label (e.g. {label}-2); runs are never mixed")
    g = get_json(f"{GATEWAY}/health")
    if not g or g.get("provider") != a.backend:
        raise SystemExit(f"the gateway on :8787 must run provider {a.backend} "
                         f"(now: {g.get('provider') if g else 'not running'}); run pflop_accuracy/check_ready.py")
    if a.judge and a.backend != "purdue":
        raise SystemExit("--judge needs Path A's gateway (the judge model, qwen3:32b, is on Purdue); grade later from a "
                         "Purdue gateway with: .venv/bin/python benchmarks/bcp/judge.py --label " + label)
    (RUNS / label).mkdir(parents=True)
    print(f"PFLOP accuracy: CLM on {a.questions} BrowseComp-Plus question(s), backend {a.backend}, label {label}")
    run([PY, "benchmarks/bcp/run.py", "--label", label, "--split", "pilot", "--arms", "clm",
         "--limit", str(a.questions), "--progress", RUNS / label / "progress.md"])
    if a.judge:
        run([PY, "benchmarks/bcp/judge.py", "--label", label])
    print(f"\nDone. Next: .venv/bin/python pflop_accuracy/check_pflops.py --label {label}")


if __name__ == "__main__":
    main()
