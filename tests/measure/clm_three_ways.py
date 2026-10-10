"""The FLOPs of one CLM-agent run, computed three ways, with the gaps between them (step 6).

  (a) ours: measure/flops.py on the gateway's calls.jsonl. Prompts rebuilt with the model's
      tokenizer and template and validated against Purdue's ids; 16-token block cache with vLLM's
      rules; Eq. 9 per call.
  (b) CLM's compute_trajectory_flops on CLM's own saved trajectory (trajectory.ctx.json), with our
      constants passed explicitly (n_body = C_token/2, n_layers 16, hidden_size 6144) and the
      measured model's tokenizer. Purdue reports no cached tokens, so CLM falls back to its
      message-level path: whole messages are its cache units, counted with the tokenizer.
  (c) CLM's block functions on our rebuilt token ids: _units_from_token_ids(ids, 16) +
      accumulate_prefill + attention_flops_from_prefill.
Also: the FLOPs CLM attached during the run (trajectory.ctx.json, final_metrics.extra), and the
run's context-edit counters (usage.json).

Run (needs CLM and transformers): .venv-clm/bin/python tests/measure/clm_three_ways.py <run folder>...
  where a run folder is runs/<label>/clm-b<budget>/<task>-r<n> (it holds run.json and harbor/).
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from clm_harness.agent_trajectory_format.models import Trajectory  # noqa: E402
from clm_harness.flops_metrics.kv_cache_flops import (_units_from_token_ids, accumulate_prefill,  # noqa: E402
                                                      attention_flops_from_prefill, compute_trajectory_flops)
from transformers import AutoTokenizer  # noqa: E402

from measure.flops import measure_log, prompt_ids  # noqa: E402
from measure.models import MEASURED, load  # noqa: E402

L_ATTN, WIDTH = 16, 6144


def three_ways(run_dir: Path, tokenizer, model: dict) -> dict:
    run = json.loads((run_dir / "run.json").read_text())
    calls_log = ROOT / run["calls_log"]
    agent_dir = next((run_dir / "harbor").glob("*/agent"))
    trial_dir = agent_dir.parent
    n_body = model["C_token"] / 2

    a = measure_log(calls_log, tokenizer, model["C_token"], model["C_attn"])
    traj = Trajectory.model_validate(json.loads((agent_dir / "trajectory.ctx.json").read_text()))
    b = compute_trajectory_flops(traj, n_body=n_body, tokenizer=MEASURED, n_layers=L_ATTN, hidden_size=WIDTH)
    in_run = ((json.loads((agent_dir / "trajectory.ctx.json").read_text()).get("final_metrics") or {})
              .get("extra") or {}).get("kv_cache_flops") or {}
    rows = [json.loads(line) for line in calls_log.read_text().splitlines()]
    rows = [r for r in rows if r.get("call_no") and r.get("status") == 200 and r.get("usage")]
    ids = [prompt_ids(tokenizer, r["request"]) for r in rows]
    G = sum(r["usage"]["completion_tokens"] for r in rows)
    pf = accumulate_prefill([_units_from_token_ids(x, 16) for x in ids])
    attn = attention_flops_from_prefill(pf, G, L_ATTN, WIDTH)
    c = {"P": pf.naive_prefill_tokens, "U": pf.cache_aware_prefill_tokens, "G": G,
         "F": 2 * n_body * (pf.cache_aware_prefill_tokens + G) + attn["cache_aware_attn_flops"],
         "decode_pairs": attn["decode_attn_pairs"]}
    usage = json.loads((agent_dir / "usage.json").read_text()) if (agent_dir / "usage.json").exists() else {}
    result = json.loads((trial_dir / "result.json").read_text()) if (trial_dir / "result.json").exists() else {}
    return {"run_id": run["run_id"], "reward": ((result.get("verifier_result") or {}).get("rewards") or {}).get("reward"),
            "usage": usage, "a": a["totals"], "rows": a["rows"], "validation": a["validation"], "b": b,
            "in_run": in_run, "c": c}


def main() -> int:
    model = load(MEASURED)
    tokenizer = AutoTokenizer.from_pretrained(MEASURED, revision=model["revision"])
    for run_dir in map(Path, sys.argv[1:]):
        r = three_ways(run_dir, tokenizer, model)
        a, b, c, u = r["a"], r["b"], r["c"], r["usage"]
        print(f"== {r['run_id']}: reward {r['reward']}; LM calls {u.get('n_lm_calls')}, bash {u.get('n_bash')}; context "
              f"edits: syncs {u.get('n_ctx_syncs')}, real {u.get('n_ctx_syncs_real')}, grew {u.get('n_ctx_grew')}, "
              f"rejected {u.get('n_ctx_rejected')}; budget {u.get('context_budget_tokens')}")
        bad = [v for v in r["validation"] if not v["ok"]]
        print(f"   validation of our rebuild: {len(r['validation']) - len(bad)}/{len(r['validation'])} pass; "
              f"dP values {sorted({v['dP'] for v in r['validation']})}" + (f"; FAILED: {bad[:3]}" if bad else ""))
        print(f"   (a) ours:            ΣP {a['P']:>8,}  Σ(P-R) {a['U']:>8,}  ΣG {a['G']:>6,}  F {a['F']:.4e}  (hit {a['hit_share']:.3f})")
        print(f"   (b) CLM on its trajectory [{b.get('prefill_source')}; tokens {b.get('generation_source')}]: ΣP "
              f"{b.get('naive_prefill_tokens'):>8,}  Σ(P-R) {b.get('cache_aware_prefill_tokens'):>8,}  ΣG {b.get('completion_tokens'):>6,}  "
              f"F {b.get('cache_aware_flops'):.4e}  (calls {b.get('n_llm_calls')}, re-prefill turns {b.get('n_reprefill_turns')})")
        if r["in_run"]:
            print(f"   (b') CLM's own, attached during the run: Σ(P-R) {r['in_run'].get('cache_aware_prefill_tokens')}, "
                  f"F {r['in_run'].get('cache_aware_flops')}, source {r['in_run'].get('prefill_source')}")
        print(f"   (c) CLM blocks on our ids: ΣP {c['P']:>8,}  Σ(P-R) {c['U']:>8,}  ΣG {c['G']:>6,}  F {c['F']:.4e}")
        ours_decode = sum(row["G"] * row["P"] + 0.5 * row["G"] ** 2 for row in r["rows"])
        decode_part = model["C_attn"] * (c["decode_pairs"] - ours_decode)
        print(f"   gap (c) - (a): {100 * (c['F'] - a['F']) / a['F']:+.3f}% of F = decode averaging "
              f"{100 * decode_part / a['F']:+.3f}% + prefill {100 * (c['F'] - a['F'] - decode_part) / a['F']:+.3f}% "
              f"(prefill tokens equal: {c['U'] == a['U']})")
        print(f"   gap (b) - (a): {100 * (b['cache_aware_flops'] - a['F']) / a['F']:+.2f}% of F "
              f"(ΣP {b.get('naive_prefill_tokens') - a['P']:+,}, Σ(P-R) {b.get('cache_aware_prefill_tokens') - a['U']:+,}, "
              f"ΣG {b.get('completion_tokens') - a['G']:+,})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
