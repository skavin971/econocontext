"""REPORT_QWEN.md: compute and outcomes of the Qwen runs, every FLOPs number from CLM's
flops_metrics. Offline.

  python -m econoclm.analysis.flops_report runs-qwen/<date>

Two FLOPs numbers per run, both with CLM's cost model (2*N_body per token plus causal
attention over CLM's attention geometry for model key 27b):

  main (replay)  the exact prompts the server received, re-tokenized: each call's
                 messages (context_snapshots) rendered with Qwen3.6's own chat template
                 (transformers apply_chat_template, the run's tools, enable_thinking=True,
                 the template's own reasoning stripping) at the served revision, then
                 walked through CLM's prefix-cache trie in vLLM's 16-token blocks
                 (flops_metrics.accumulate_prefill) and priced by attention_flops_from_prefill.
  cross-check    CLM's own trajectory.ctx.json kv_cache_flops, which uses the server's
                 cached-token counts when every call reports them ("server_usage_cached").

If the two differ by more than 5% for any arm, REPORT_QWEN.md says so at the top.
Per arm and per task (mean and min-max over runs): pass rate, PFLOPs (both), PFLOPs per
solved task, prompt tokens computed vs reused, generated tokens, calls, edits, rollbacks,
calls whose generation reached max_tokens (CLM does not record finish reasons), peak
prompt, econo use (EconoCLM-Tools), view use (EconoCLM-View); the system-prompt check per
arm; and every version and setting (vLLM, model revision, GPU, driver, CUDA, commits,
arm configs), so later arms can be checked against this setup.
"""

import argparse
import csv
import hashlib
import json
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from clm_harness.flops_metrics import kv_cache_flops as kvf
from clm_harness.utils.tool_schemas import BASH_TOOL

from .common import load_runs

NAMES = {"clm": "CLM", "econotools": "EconoCLM-Tools", "econoview": "EconoCLM-View"}
MODEL_KEY = "27b"
FLAG_DIFF = 0.05


def tokenizer(revision: str | None):
    try:
        from transformers import AutoTokenizer
    except ImportError:
        return None, "transformers is not installed (pip install -e ../context-language-models[hf])"
    try:
        return AutoTokenizer.from_pretrained("Qwen/Qwen3.6-27B", revision=revision), None
    except Exception as exc:
        return None, f"tokenizer unavailable: {exc}"


def as_served(msgs: list[dict]) -> list[dict]:
    """The messages as vLLM hands them to the chat template: tool-call arguments parsed
    from their JSON string into a dict (vLLM does this before templating)."""
    out = []
    for m in msgs:
        m = dict(m)
        if m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                tc = json.loads(json.dumps(tc))
                fn = tc.get("function") or {}
                if isinstance(fn.get("arguments"), str):
                    try:
                        fn["arguments"] = json.loads(fn["arguments"] or "{}")
                    except ValueError:
                        pass
                calls.append(tc)
            m["tool_calls"] = calls
        out.append(m)
    return out


def replay_flops(snaps: list[list[dict]], gen: int, tok) -> dict:
    turns = []
    for msgs in snaps:
        ids = tok.apply_chat_template(as_served(msgs), tools=[BASH_TOOL], add_generation_prompt=True,
                                      tokenize=True, enable_thinking=True)
        if hasattr(ids, "keys"):                     # transformers >= 5 returns a BatchEncoding
            ids = ids["input_ids"]
        if ids and isinstance(ids[0], list):          # a batch of one
            ids = ids[0]
        turns.append(kvf._units_from_token_ids(list(ids), kvf.DEFAULT_BLOCK_SIZE))
    pf = kvf.accumulate_prefill(turns)
    n_body = kvf.resolve_n_body(MODEL_KEY, required=True)
    layers, width = kvf.resolve_attn_geometry(MODEL_KEY)
    attn = kvf.attention_flops_from_prefill(pf, gen, layers, width)
    return {"flops": 2.0 * n_body * (pf.cache_aware_prefill_tokens + gen) + attn["cache_aware_attn_flops"],
            "computed": pf.cache_aware_prefill_tokens,
            "reused": pf.naive_prefill_tokens - pf.cache_aware_prefill_tokens}


def run_row(run, tok, max_tokens: int) -> dict:
    agent = run.agent_dir()
    ctx = json.loads((agent / "trajectory.ctx.json").read_text()) if agent and (agent / "trajectory.ctx.json").exists() else {}
    kv = ((ctx.get("final_metrics") or {}).get("extra") or {}).get("kv_cache_flops") or {}
    steps = [s for seg in ctx.get("segments") or [] for s in seg.get("steps") or [] if s.get("metrics")]
    usage = run.usage or {}
    gen = sum((s["metrics"].get("completion_tokens") or 0) for s in steps) or int(kv.get("completion_tokens") or 0)
    snaps = run.snapshots()
    rep = None
    if tok is not None and snaps:
        try:
            rep = replay_flops(snaps, gen, tok)
        except Exception as exc:
            rep = {"error": str(exc)}
    row = {"arm": run.arm, "task": run.task, "rep": run.rep, "passed": int(run.reward == 1),
           "reward": run.reward, "exception": run.exception or "",
           "pflops_replay": (rep or {}).get("flops", 0) / 1e15 if rep and "flops" in rep else None,
           "pflops_server": (kv.get("cache_aware_flops") or 0) / 1e15 if kv.get("cache_aware_flops") else None,
           "server_source": kv.get("prefill_source"),
           "computed_replay": (rep or {}).get("computed"), "reused_replay": (rep or {}).get("reused"),
           "computed_server": kv.get("cache_aware_prefill_tokens"),
           "reused_server": kv.get("cache_hit_tokens"),
           "generated": gen, "calls": kv.get("n_llm_calls") or usage.get("n_lm_calls"),
           "edits": usage.get("n_ctx_syncs_real"), "rollbacks": usage.get("n_retry_on_limit"),
           "max_tokens_hit": sum((s["metrics"].get("completion_tokens") or 0) >= max_tokens for s in steps),
           "peak_prompt": max((s["metrics"].get("prompt_tokens") or 0) for s in steps) if steps else None,
           "replay_error": (rep or {}).get("error")}
    store = run.store()
    if run.arm == "econotools" and store is not None:
        ops = {o["op"]: o["n"] for o in store.rows("SELECT op, COUNT(*) n FROM econo_ops GROUP BY op")}
        row["econo_use"] = sum(ops.values())
    if run.arm == "econoview":
        log = run.dir / "view_log.jsonl"
        recs = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
        row["view_edits"] = sum(r.get("kind") == "edit" for r in recs)
    return row


def system_prompt_check(runs) -> list[str]:
    def first_system(run):
        d = run.agent_dir()
        snaps = sorted((d / "context_snapshots").glob("turn-*.json")) if d else []
        return json.loads(snaps[0].read_text())["messages"][0]["content"] if snaps else None
    from ..arms.econo_view.agent import swap_section
    clm = {r.task: first_system(r) for r in runs if r.arm == "clm"}
    out = []
    for arm in ("clm", "econotools", "econoview"):
        sel = [r for r in runs if r.arm == arm]
        if not sel:
            continue
        hashes = sorted({hashlib.sha256((first_system(r) or "").encode()).hexdigest()[:12] for r in sel})
        ok = n = 0
        for r in sel:
            s, base = first_system(r), clm.get(r.task)
            if s is None:
                continue
            n += 1
            if arm == "clm":
                ok += "## Managing your context" in s and "LIVE_CTX_MAIN.txt" in s
            elif base is not None and arm == "econotools":
                ok += s.split("\n\n---\n\n")[0] == base
            elif base is not None:
                ok += s == swap_section(base, base.split("Your context budget is ")[1].split(";")[0])
        out.append(f"- {NAMES[arm]}: {ok}/{n} runs carry the expected system prompt; prompt hashes: {', '.join(hashes)}")
    return out


def environment(out_dir: Path) -> list[str]:
    def sh(cmd):
        try:
            return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30).stdout.strip() or "n/a"
        except Exception:
            return "n/a"
    settings = json.loads((out_dir / "settings.json").read_text()) if (out_dir / "settings.json").exists() else {}
    srv = settings.get("server_settings") or {}
    repo = Path(__file__).resolve().parents[2]
    lines = ["| Item | Value |", "|---|---|",
             f"| vLLM | {srv.get('vllm_version', 'n/a')} (live: {settings.get('vllm_version_live', 'n/a')}) |",
             f"| Model | {srv.get('model', 'n/a')} @ {srv.get('revision', 'n/a')} (served as {srv.get('served_model_name', 'n/a')}) |",
             f"| Server flags | `{srv.get('flags', 'n/a')}` |",
             f"| GPU, driver | {srv.get('gpu') or sh('nvidia-smi --query-gpu=name,driver_version --format=csv,noheader')} |",
             f"| CUDA | {srv.get('cuda', 'n/a')}; torch {srv.get('torch', 'n/a')} |",
             f"| Our repo | {sh(f'git -C {repo} rev-parse --short HEAD')} ({sh(f'git -C {repo} branch --show-current')}) |",
             f"| CLM | {sh(f'git -C {repo.parent / 'context-language-models'} rev-parse --short HEAD')} |",
             f"| TBLite | {sh(f'git -C {repo.parent / 'OpenThoughts-TBLite'} rev-parse --short HEAD')} |",
             f"| Harbor | {sh(f'{sys.executable} -m pip show harbor | grep ^Version')} |",
             f"| Python | {sys.version.split()[0]} |",
             f"| Arms, reps, workers | {settings.get('arms')}, {settings.get('reps')}, {settings.get('workers')}; timeout multiplier {settings.get('timeout_multiplier')} |"]
    for arm, cfg in (settings.get("configs") or {}).items():
        lines.append(f"| Config {arm} | `{json.dumps(cfg, sort_keys=True)}` |")
    return lines


def agg(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return "–"
    return f"{statistics.mean(v):.3g} ({min(v):.3g}–{max(v):.3g})" if len(v) > 1 else f"{v[0]:.3g}"


def report(out_dir: Path) -> str:
    trials = out_dir / "trials"
    runs = load_runs(trials)
    settings = json.loads((out_dir / "settings.json").read_text()) if (out_dir / "settings.json").exists() else {}
    revision = (settings.get("server_settings") or {}).get("revision")
    max_tokens = int((next(iter((settings.get("configs") or {}).values()), {}).get("agent_kwargs") or {}).get("max_tokens", 2048))
    tok, tok_err = tokenizer(revision)
    rows = [run_row(r, tok, max_tokens) for r in runs]
    with open(out_dir / "results_qwen.csv", "w", newline="") as f:
        keys = sorted({k for r in rows for k in r})
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    by_arm = defaultdict(list)
    for r in rows:
        by_arm[r["arm"]].append(r)
    flags = []
    if tok is None:
        flags.append(f"**The main replay did not run: {tok_err}.** Only the server-count cross-check is shown.")
    for arm, rs in by_arm.items():
        rep = sum(r["pflops_replay"] or 0 for r in rs)
        srv = sum(r["pflops_server"] or 0 for r in rs)
        if tok is not None and srv and abs(rep - srv) / srv > FLAG_DIFF:
            flags.append(f"**{NAMES.get(arm, arm)}: replay and server-count FLOPs differ by "
                         f"{(rep - srv) / srv:+.1%} (more than 5%).**")
        sources = {r["server_source"] for r in rs}
        if sources - {"server_usage_cached"}:
            flags.append(f"**{NAMES.get(arm, arm)}: CLM's FLOPs source was {sorted(s for s in sources if s)} "
                         "for some runs, not server_usage_cached (the server did not report cached tokens on every call).**")
    L = ["# REPORT_QWEN: Qwen3.6-27B on 10 TBLite tasks", ""]
    if flags:
        L += ["## Flags", ""] + [f"- {x}" for x in flags] + [""]
    L += ["## Per arm", "", "| Arm | Runs | Pass rate | PFLOPs (replay) | PFLOPs (server counts) | Replay − server | "
          "PFLOPs per solved task (replay) | Prompt tokens computed / reused (replay) | Generated | Calls | Edits | "
          "Rollbacks | Calls at max_tokens | Peak prompt | econo use | View edits |",
          "|" + "---|" * 16]
    for arm, rs in sorted(by_arm.items(), key=lambda kv: list(NAMES).index(kv[0]) if kv[0] in NAMES else 9):
        n, solved = len(rs), sum(r["passed"] for r in rs)
        rep = sum(r["pflops_replay"] or 0 for r in rs)
        srv = sum(r["pflops_server"] or 0 for r in rs)
        diff = f"{(rep - srv) / srv:+.1%}" if srv and tok is not None else "–"
        L.append(f"| {NAMES.get(arm, arm)} | {n} | {solved}/{n} | {rep:.2f} | {srv:.2f} | {diff} | "
                 f"{(rep / solved) if solved else float('nan'):.2f} | "
                 f"{sum(r['computed_replay'] or 0 for r in rs):,} / {sum(r['reused_replay'] or 0 for r in rs):,} | "
                 f"{sum(r['generated'] or 0 for r in rs):,} | {sum(r['calls'] or 0 for r in rs)} | "
                 f"{sum(r['edits'] or 0 for r in rs)} | {sum(r['rollbacks'] or 0 for r in rs)} | "
                 f"{sum(r['max_tokens_hit'] for r in rs)} | {max((r['peak_prompt'] or 0) for r in rs):,} | "
                 f"{sum(r.get('econo_use', 0) for r in rs) if arm == 'econotools' else '–'} | "
                 f"{sum(r.get('view_edits', 0) for r in rs) if arm == 'econoview' else '–'} |")
    L += ["", "## Per task (mean, min–max over runs)", "",
          "| Task | Arm | Pass rate | PFLOPs (replay) | PFLOPs (server) | Calls | Edits | Peak prompt |", "|---|---|---|---|---|---|---|---|"]
    tasks = sorted({r["task"] for r in rows})
    for t in tasks:
        for arm in NAMES:
            rs = [r for r in rows if r["task"] == t and r["arm"] == arm]
            if rs:
                L.append(f"| {t} | {NAMES[arm]} | {sum(r['passed'] for r in rs)}/{len(rs)} | {agg([r['pflops_replay'] for r in rs])} | "
                         f"{agg([r['pflops_server'] for r in rs])} | {agg([r['calls'] for r in rs])} | "
                         f"{agg([r['edits'] for r in rs])} | {agg([r['peak_prompt'] for r in rs])} |")
    L += ["", "## System prompts", ""] + system_prompt_check(runs)
    L += ["", "## Setup (versions and settings)", ""] + environment(out_dir)
    L += ["", "Notes: \"Calls at max_tokens\" counts calls whose generated tokens reached max_tokens "
          "(CLM does not record finish reasons; there is no gateway on Qwen). Qwen3.6's chat template strips "
          "earlier turns' reasoning, which limits prefix reuse in every arm alike."]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out_dir", type=Path)
    args = ap.parse_args()
    text = report(args.out_dir)
    (args.out_dir / "REPORT_QWEN.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
