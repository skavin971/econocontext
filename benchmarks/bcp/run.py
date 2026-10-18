"""The BrowseComp-Plus runner (step 13a): our harness (base) and CLM's own agent, one question at a time, through our
gateway, on the one local search service. Everything that holds BrowseComp-Plus text (task folders, trials, the
gateway's logs for run ids starting with "bcp") lives in ~/econo/bcp/, never in the repo.

Settings (docs/tier-a-decisions.md, step 13a): budget 23,560 with a 2,048 reserve (the CLM paper, App. E), 100 turns,
4,096 output tokens (the gateway pins it for runs starting with "bcp"), observations up to 60,000 characters
(CLM's bcp.yaml: "search results and documents arrive whole"); search: top 10, 512-token snippets, documents capped
at 8,192 tokens (search_server.py).
- base: harness.agent:Agent with Base, the ours interface and the bcp tool set (bcp_search, bcp_get_document).
- clm: CLM's agent as released (clm-harbor -a clm-minimal --clm-config bcp, CLM's bcp.yaml untouched), with the
  budget and turn cap given as agent kwargs and the search skill from benchmarks/bcp/skill/bcp_search.
Run order alternates per question: base first on even positions, clm first on odd ones.

Use (the gateway with --max-tokens-for bcp=4096 --log-dir-for bcp=~/econo/bcp/gateway, and search_server.py, up):
  .venv/bin/python benchmarks/bcp/run.py --label bcp13p --split pilot --arms base,clm
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BCP = Path.home() / "econo" / "bcp"
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("tblite_run", ROOT / "benchmarks" / "tblite" / "run.py")
tblite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tblite)

BUDGET, TURNS, MAX_TOKENS, OUTPUT_CHARS = 23_560, 100, 4096, 60_000


def run_trial(a, arm: str, qid: str, repeat: int) -> dict:
    run_id = f"{a.label}.{arm}.{qid}.r{repeat}"
    out = BCP / "runs" / a.label / arm / f"{qid}-r{repeat}"
    out.mkdir(parents=True, exist_ok=True)
    h = tblite.health(a.gateway, wait_s=600)
    if h is None:
        return {"run_id": run_id, "skipped": "the gateway did not answer /health"}
    if (h.get("max_tokens_for") or {}).get("bcp") != MAX_TOKENS:
        raise SystemExit("the gateway must pin 4,096 output tokens for bcp runs (--max-tokens-for bcp=4096)")
    if h["max_requests_per_day"] - h["requests_today"] < h["max_calls_per_run"]:
        return {"run_id": run_id, "skipped": "the daily request budget cannot hold a full run"}
    task = BCP / "tasks" / qid
    name = f"{arm}-{qid}-r{repeat}"
    env = {**os.environ, "PYTHONPATH": f"{ROOT}:{os.environ.get('PYTHONPATH', '')}"}
    if arm == "clm":
        const = json.loads((ROOT / "measure" / "model_constants.json").read_text())
        hf_id = const["measured_model"]
        command = [str(ROOT / ".venv-clm" / "bin" / "clm-harbor"), "trial", "start", "-p", str(task), "-e", "docker",
                   "-a", "clm-minimal", "-m", f"openai/{a.model}", "--clm-config", "bcp",
                   "--agent-kwarg", f"api_base={a.gateway}/run/{run_id}/v1",
                   "--agent-kwarg", f"context_budget_tokens={BUDGET}", "--agent-kwarg", f"max_steps={TURNS}",
                   "--agent-kwarg", f"skill_dirs={HERE / 'skill' / 'bcp_search'}",
                   "--agent-kwarg", "cost_metric=flops", "--agent-kwarg", "flops_model_key=27b",
                   "--agent-kwarg", f"flops_n_body={const['models'][hf_id]['C_token'] // 2}",
                   "--agent-kwarg", f"flops_tokenizer={hf_id}",
                   "--trials-dir", str(out / "harbor"), "--trial-name", name]
    else:
        command = [str(ROOT / ".venv" / "bin" / "harbor"), "trial", "start", "-p", str(task), "-e", "docker",
                   "-a", "harness.agent:Agent", "-m", f"openai/{a.model}",
                   "--agent-kwarg", f"api_base={a.gateway}/run/{run_id}/v1", "--agent-kwarg", f"run_id={run_id}",
                   "--agent-kwarg", "policy=baselines.base.policy:Base", "--agent-kwarg", "interface=ours",
                   "--agent-kwarg", "tool_set=bcp", "--agent-kwarg", f"budget={BUDGET}",
                   "--agent-kwarg", f"max_steps={TURNS}", "--agent-kwarg", f"max_tokens={MAX_TOKENS}",
                   "--agent-kwarg", f"output_chars={OUTPUT_CHARS}", "--agent-kwarg", "budget_margin=true",
                   "--trials-dir", str(out / "harbor"), "--trial-name", name]
    (out / "command.txt").write_text(" ".join(command) + "\n")
    started = time.time()
    with (out / "harbor.out").open("w") as log:
        code = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT).returncode
    return finish(a, arm, qid, repeat, code, time.time() - started, h)


def finish(a, arm: str, qid: str, repeat: int, code: int, seconds: float, h: dict | None = None) -> dict:
    """The trial's summary and validation (also for a trial whose runner was stopped: --summarize-only)."""
    run_id, name = f"{a.label}.{arm}.{qid}.r{repeat}", f"{arm}-{qid}-r{repeat}"
    out = BCP / "runs" / a.label / arm / f"{qid}-r{repeat}"
    h = h or tblite.health(a.gateway, wait_s=60) or {}
    log_dir = Path(h["log_dir_for"]["bcp"]) if (h.get("log_dir_for") or {}).get("bcp") else BCP / "gateway"
    summary = tblite.summarize(run_id, arm, arm == "clm", qid, repeat, out, out / "harbor" / name,
                               log_dir / run_id / "calls.jsonl", code, seconds)
    m = tblite.measure(summary) if (log_dir / run_id / "calls.jsonl").exists() else {}
    summary.update(valid=m.get("valid"), invalid=m.get("invalid"), PF=(m.get("F") or 0) / 1e15)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="bcp13p")
    ap.add_argument("--split", default="pilot")
    ap.add_argument("--arms", default="base,clm")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--start", type=int, default=0, help="the first question's position in the split")
    ap.add_argument("--limit", type=int, help="run at most this many questions (a first check)")
    ap.add_argument("--gateway", default="http://127.0.0.1:8787")
    ap.add_argument("--model", default="qwen3.8:27b")
    ap.add_argument("--progress", default=str(ROOT / "runs" / "tier-a" / "step13a-progress.md"))
    ap.add_argument("--summarize-only", metavar="ARM:QID", help="summarize a trial whose runner was stopped")
    a = ap.parse_args()
    if a.summarize_only:
        arm, qid = a.summarize_only.split(":")
        trial = BCP / "runs" / a.label / arm / f"{qid}-r{a.repeat}" / "harbor" / f"{arm}-{qid}-r{a.repeat}"
        result = json.loads((trial / "result.json").read_text()) if (trial / "result.json").exists() else {}
        seconds = 0.0
        if result.get("started_at") and result.get("finished_at"):
            from datetime import datetime
            seconds = (datetime.fromisoformat(result["finished_at"]) - datetime.fromisoformat(result["started_at"])).total_seconds()
        s = finish(a, arm, qid, a.repeat, 0, seconds)
        print(json.dumps({k: s.get(k) for k in ("run_id", "calls", "peak_P", "seconds", "valid", "invalid", "end")}))
        return
    if not a.label.startswith("bcp"):
        raise SystemExit("labels start with 'bcp' (the gateway keeps their logs and output cap apart)")
    ids = json.loads((HERE / "split.json").read_text())[a.split]
    positions = list(range(a.start, len(ids) if a.limit is None else min(len(ids), a.start + a.limit)))
    arms = a.arms.split(",")
    tblite.keep_awake()
    for i in positions:
        qid = ids[i]
        order = arms if i % 2 == 0 else list(reversed(arms))
        for arm in order:
            s = run_trial(a, arm, qid, a.repeat)
            line = (f"{time.strftime('%H:%M:%S')} {s['run_id']}: " + (s.get("skipped") or
                    f"exit {s.get('exit')}, {s.get('calls')} calls, peak {s.get('peak_P')}, {s.get('seconds')} s, "
                    f"valid {s.get('valid')}/{(s.get('valid') or 0) + (s.get('invalid') or 0)}, {s.get('PF', 0):.3f} PF, "
                    f"end {s.get('end')}, exception {s.get('exception')}"))
            print(line, flush=True)
            with open(a.progress, "a") as f:
                f.write(line + "\n")


if __name__ == "__main__":
    main()
