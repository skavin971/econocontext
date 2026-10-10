"""Cross-check our prefix cache and attention pairs against CLM's own code, on synthetic token-id runs.

Why it exists: measure/ reimplements CLM's FLOPs accounting. CLM's code
(third_party/context-language-models, clm_harness.flops_metrics.kv_cache_flops) is the reference:
- `_units_from_token_ids(ids, 16)` + `accumulate_prefill` must give exactly our ΣP and Σ(P − R),
  and our prefill attention pairs Σ½(P² − R²);
- decode differs by design: CLM averages generation over turns (g·ΣP + G²/2n, with g = G/n), while
  Eq. 9 uses each call's own G_t (Σ G_t·P_t + ½G_t²). The gap is reported, not asserted;
- identical repeated prompts are the one known prefill difference. CLM matches the trailing partial
  block, we don't; reported separately.

The runs are random but seeded (growth, mid-prompt edits, compaction-like resets, short prompts).
Run (needs CLM): .venv-clm/bin/python tests/measure/clm_crosscheck.py [--from-calls <calls.jsonl> ...]
  --from-calls checks real runs instead, rebuilding their token ids with measure/flops.py (step 6).
Exit code 0 when prefill agrees exactly; it skips (exit 0) without CLM.
"""

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from measure.cache_sim import cached_prefix  # noqa: E402

try:
    from clm_harness.flops_metrics.kv_cache_flops import (_units_from_token_ids, accumulate_prefill,
                                                          attention_flops_from_prefill)
except ImportError as exc:
    print(f"skipped: CLM is not installed ({exc})")
    sys.exit(0)

L_ATTN, ATTN_WIDTH = 16, 6144          # Qwen3.6/3.8-27B: 16 full-attention layers, h_q·d_h = 24·256


def synthetic_runs(seed: int = 20261010, n: int = 60) -> list[tuple[str, list[list[int]], list[int]]]:
    rng = random.Random(seed)
    runs = []
    for k in range(n):
        prompt, prompts, gens = [rng.randrange(1, 248_000) for _ in range(rng.randrange(20, 400))], [], []
        for _ in range(rng.randrange(2, 30)):
            move = rng.random()
            if move < 0.05:                                         # an identical repeat (a resent call)
                pass
            elif move < 0.6:                                        # the agent appends a tool result
                prompt = prompt + [rng.randrange(1, 248_000) for _ in range(rng.randrange(1, 900))]
            elif move < 0.8:                                        # an edit in the middle (eviction)
                at = rng.randrange(len(prompt))
                prompt = prompt[:at] + [rng.randrange(1, 248_000) for _ in range(rng.randrange(1, 40))] + prompt[at + 1:]
            elif move < 0.9:                                        # compaction-like: keep a head, new body
                prompt = prompt[:rng.randrange(1, 200)] + [rng.randrange(1, 248_000) for _ in range(rng.randrange(1, 300))]
            else:                                                   # a short prompt
                prompt = [rng.randrange(1, 248_000) for _ in range(rng.randrange(1, 16))]
            prompts.append(list(prompt))
            gens.append(rng.randrange(1, 2000))
        runs.append((f"synthetic {k}", prompts, gens))
    return runs


def compare(name, prompts, gens) -> tuple[bool, str]:
    R = cached_prefix(prompts)
    ours = {"P": sum(map(len, prompts)), "U": sum(len(p) - r for p, r in zip(prompts, R)),
            "pairs": sum(0.5 * (len(p) ** 2 - r ** 2) for p, r in zip(prompts, R)),
            "decode": sum(g * len(p) + 0.5 * g * g for p, g in zip(prompts, gens))}
    pf = accumulate_prefill([_units_from_token_ids(p, 16) for p in prompts])
    decode = attention_flops_from_prefill(pf, sum(gens), L_ATTN, ATTN_WIDTH)["decode_attn_pairs"]
    repeats = sum(1 for i, p in enumerate(prompts) if any(p == q for q in prompts[:i]))
    prefill_equal = (ours["P"], ours["U"], ours["pairs"]) == (pf.naive_prefill_tokens, pf.cache_aware_prefill_tokens,
                                                              pf.cache_aware_attn_pairs)
    note = (f"{name}: {len(prompts)} calls, ΣP {ours['P']:,} (CLM {pf.naive_prefill_tokens:,}), Σ(P-R) {ours['U']:,} "
            f"(CLM {pf.cache_aware_prefill_tokens:,}), prefill pairs {'equal' if ours['pairs'] == pf.cache_aware_attn_pairs else 'DIFFERENT'}, "
            f"decode pairs ours {ours['decode']:,.0f} vs CLM-averaged {decode:,.0f} ({100 * (decode - ours['decode']) / ours['decode']:+.1f}%)")
    return prefill_equal or bool(repeats), note + (f"; {repeats} identical repeat(s)" if repeats else "")


def from_calls(paths: list[str]) -> list[tuple[str, list[list[int]], list[int]]]:
    from transformers import AutoTokenizer
    from measure.flops import prompt_ids
    from measure.models import MEASURED, load
    tokenizer = AutoTokenizer.from_pretrained(MEASURED, revision=load()["revision"])
    runs = []
    for path in paths:
        rows = [json.loads(line) for line in Path(path).read_text().splitlines()]
        rows = [r for r in rows if r.get("call_no") and r.get("status") == 200 and r.get("usage")]
        runs.append((Path(path).parent.name, [prompt_ids(tokenizer, r["request"]) for r in rows],
                     [r["usage"]["completion_tokens"] for r in rows]))
    return runs


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--from-calls", nargs="*", help="real runs' calls.jsonl instead of synthetic runs")
    a = p.parse_args()
    runs = from_calls(a.from_calls) if a.from_calls else synthetic_runs()
    agree, failures, explained, decode = 0, 0, [], []
    for name, prompts, gens in runs:
        ok, note = compare(name, prompts, gens)
        R = cached_prefix(prompts)
        ours_uncached = sum(map(len, prompts)) - sum(R)
        pf = accumulate_prefill([_units_from_token_ids(q, 16) for q in prompts])
        ours_decode = sum(g * len(q) + 0.5 * g * g for q, g in zip(prompts, gens))
        decode.append(100 * (attention_flops_from_prefill(pf, sum(gens), L_ATTN, ATTN_WIDTH)["decode_attn_pairs"]
                             - ours_decode) / ours_decode)
        # The known difference: CLM also matches an identical repeat's trailing partial block.
        partial = sum(len(q) % 16 for i, q in enumerate(prompts) if any(q == r for r in prompts[:i]))
        if ours_uncached == pf.cache_aware_prefill_tokens and ok:      # ok: ΣP, Σ(P-R) and prefill pairs all equal
            agree += 1
        elif ours_uncached == pf.cache_aware_prefill_tokens:
            failures += 1
            print("UNEXPLAINED (attention pairs)", note)
        elif ours_uncached - pf.cache_aware_prefill_tokens == partial:
            explained.append(partial)
        else:
            failures += 1
            print("UNEXPLAINED", note)
        if a.from_calls:
            print(note)
    decode.sort()
    print(f"{len(runs)} runs: prefill exactly equal in {agree}; equal except identical repeats in {len(explained)}, "
          f"where our extra uncached tokens equal Σ(P mod 16) of the repeats exactly ({explained}); "
          f"unexplained differences: {failures}")
    print(f"decode attention pairs, CLM's averaged formula vs per-call G_t: median {decode[len(decode) // 2]:+.1f}%, "
          f"range {decode[0]:+.1f}% to {decode[-1]:+.1f}%")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
