# Step 19 report: cache-read prices, and cache-aware variants of observation masking (offline, 2026-10-13)

**Branch:** `agentdb-12a`.
- Step 18 was approved and pushed after the secrets gate (`a3c8eac`; `step19-0-push.out`).
- Step 19 is one local commit, **not pushed**.

The step made no model calls and no code changes outside the analysis scripts.
- **Scripts:** `runs/tier-a/scripts/step19_variants.py` and `step19_summary.py`.
- **Tables:** `runs/tier-a/step19/summary.md`.
- **Data:**
  - `<model>_instances.csv` (instance ids and numbers);
  - `<model>_lengths.json`;
  - `provider_cache_prices.md` (official prices, with URLs and the retrieval date).

**Everything here is a fixed-trajectory estimate on the canonical track** (step 18's engine).

## The answer
**Yes: age-1 stubbing beats both raw and masking in FLOPs and in dollars at 10% and 40% cache reads, on all three models, and keeps prompts shorter than masking's.** The variant is V2r: stub an observation once the reply after it has not used it, and restore it at the tail if a later reply needs it.

| model | V2r FLOPs / raw | / masking arm | V2r $ at 10% / raw | / masking | V2r $ at 40% / raw | / masking | median prompt / masking's | restores |
|---|---|---|---|---|---|---|---|---|
| Qwen3-Coder-480B | 0.96 | 0.14 | 0.33 | 0.27 | 0.23 | 0.51 | 0.71 | 1,620 |
| Qwen3-32B | 0.88 | 0.11 | 0.41 | 0.21 | 0.29 | 0.37 | 0.54 | 343 |
| Gemini 2.5 Flash | 0.78 | 0.17 | 0.30 | 0.30 | 0.20 | 0.54 | 0.56 | 371 |

- **V2r also beats masking applied to the same trajectory** on every metric.
- **With restores, no reply ever lacks an observation it needs.** Masking itself leaves 195–1,212 replies needing an observation it had masked.
- **Without restores (V2):** it is cheaper still, but 492–2,107 replies need a stubbed observation.
- **Two-zone at the masking median as cap (V3):** it also passes on all three models, but leaves 436–2,820 such replies.
- **Block masking (V1, their rule every k turns):** it beats masking but not raw in FLOPs: 1.07–2.0× raw. Recomputing every 5–20 turns still breaks the cache too often.

## 1. Cache-read price sweep (arms as run, 499–500 paired instances)

Cost ratio against raw, by cache-read price as a share of input:

| | 0% | 5% | 10% | 13% | 20% | 25% | 40% | 50% | 100% |
|---|---|---|---|---|---|---|---|---|---|
| Qwen3-Coder: masking / raw | 5.12 | 1.91 | 1.21 | 1.01 | 0.74 | 0.63 | 0.45 | 0.39 | 0.27 |
| Qwen3-Coder: summary / raw | 1.69 | 0.85 | 0.66 | 0.61 | 0.54 | 0.51 | 0.46 | 0.45 | 0.41 |
| Qwen3-32B: masking / raw | 8.21 | 3.05 | 1.96 | 1.64 | 1.22 | 1.05 | 0.78 | 0.69 | 0.49 |
| Qwen3-32B: summary / raw | 1.78 | 0.88 | 0.69 | 0.64 | 0.56 | 0.54 | 0.49 | 0.47 | 0.44 |
| Gemini 2.5 Flash: masking / raw | 5.80 | 1.63 | 0.99 | 0.82 | 0.59 | 0.51 | 0.37 | 0.32 | 0.22 |
| Gemini 2.5 Flash: summary / raw | 2.40 | 0.93 | 0.70 | 0.64 | 0.56 | 0.53 | 0.48 | 0.46 | 0.43 |

- **Masking breaks even with raw** at about 13% (Qwen3-Coder), 27% (Qwen3-32B) and 10% (Gemini).
  - On the same trajectory: 12%, 18% and 12%.
  - Below that it costs more, up to 1.6–3× at 5%.
- **LLM-Summary is cheaper than raw at any price above about 3–5%.** At 0% it costs 1.7–2.4× raw.

**Official cache-read prices today** (`provider_cache_prices.md`, retrieved 2026-10-13):

| provider | standard share | exceptions |
|---|---|---|
| OpenAI | 10% | 5% on gpt-6.1-sol |
| Anthropic | 10% | 5% on Opus 5.5 and Sonnet 5.5; 2.5% on Fable/Mythos 5.1 |
| Google Gemini | 10% | 5% on Gemini 4 Argon |
| DeepSeek | about 2% (V4-Flash) | not loaded directly (timeouts); taken from the official page's search text |
| Together | 10–12.5% | 20% on MiniMax M3 |

**At every major provider's standard price, masking is at best break-even with raw, and at the newer 2–5% prices it costs 1.6–3× raw.** The Complexity Trap's own cards were 40% (Qwen3-Coder), 100% (Qwen3-32B), and no discount in effect for Gemini (step 18).

## 2. Variants on the raw arm's trajectory (Qwen3-Coder; the other models are in `summary.md`)

| policy | tokens | FLOPs | $ at 10% | $ at 25% | $ at 40% | median prompt | p90 prompt | replies needing a stubbed observation | restores |
|---|---|---|---|---|---|---|---|---|---|
| raw (as run) | 979M | 1,725 PF | $210 | $458 | $705 | 24,831 | 68,420 | 0 | 0 |
| masking (arm as run) | 413M | 11,489 PF | $256 | $288 | $321 | 12,244 | 21,735 | – | – |
| LLM-Summary (arm as run) | 633M | 3,572 PF | $139 | $232 | $326 | 16,946 | 28,392 | – | – |
| masking, same trajectory | 384M | 10,698 PF | $236 | $265 | $295 | 12,039 | 21,122 | 1,212 | 0 |
| V1 block masking k=5 | 419M | 3,430 PF | $111 | $168 | $225 | 13,344 | 23,093 | 1,069 | 0 |
| V1 k=10 | 460M | 2,486 PF | $100 | $167 | $235 | 14,818 | 25,698 | 928 | 0 |
| V1 k=20 | 525M | 2,119 PF | $105 | $190 | $274 | 16,518 | 30,873 | 761 | 0 |
| **V2 age-1 stubs** | 258M | **1,455 PF** | **$58** | $94 | $130 | 7,137 | 16,703 | 2,107 | 0 |
| **V2r, with restores** | 316M | 1,651 PF | $70 | $117 | $164 | 8,709 | 20,449 | **0** | 1,620 |
| V3 two-zone, cap 12,244 (masking median) | 291M | 1,659 PF | $64 | $104 | $143 | 9,765 | **13,406** | 2,820 | 0 |
| V3 two-zone, cap 21,735 (masking p90) | 427M | 1,802 PF | $79 | $139 | $199 | 15,229 | 20,766 | 1,611 | 0 |

**Why V2 and V3 keep the cache:**
- **V2** stubs each observation once, one turn after it arrives, so the prompt changes only at its tail.
- **V3** stubs only when the cap is passed, and from the newest end.

Masking, by contrast, changes the prompt about 10 turns back on every call.

## Method (what I had to decide)
- **Data:** I downloaded five more archives under your 1 GB rule (3.1 GB, to `~/econo/ctrap/data/`). Your message assumed they were already there; only the Gemini masking archive was.
  - Qwen3-32B raw, masking and summary, non-thinking;
  - Gemini 2.5 Flash raw and summary, non-thinking.
  - All three models' trajectories have the same structure.
- **The raw agent's prompts are append-only** (checked on every turn of every instance). So each variant is a transformation of one instance history: their placeholder "Old environment output: (N lines omitted)", the task observation never stubbed, and replay with step 18's engine. Replaying raw through it reproduces raw exactly.
- **V1:** SWE-agent's own `polling = k` (their rule recomputed only every k turns).
- **V2:** an observation is stubbed when it is no longer the newest, unless the reply right after it referenced it.
- **V2r, my addition:** as V2, plus a full restore at the tail when a later reply needs a stubbed observation.
- **V3:**
  - when the prompt passes the cap, stub cheapest first (least text after), never the newest, down to 60% of the cap;
  - caps are the masking arm's median and p90 prompt length in our tokens (Qwen3-Coder 12,244 / 21,735).
- **"Referenced" and "needed" use step 17's unique rule:** a quote of 30+ characters, or a path, identifier or multi-word name, that no other earlier message contains.
- **Dollars:** each model's input and output card, with cache reads at the stated share of input.
  - Qwen3-Coder: their tiered card.
  - Qwen3-32B: $0.70 / $2.80.
  - Gemini 2.5 Flash: $0.30 / $2.50.

## Caveats
- **Fixed trajectories.** On a stubbed prompt the agent would act differently. V2r's restores cover only the needs we can detect.
- **V2's "next reply" signal is cheap to compute online, but it is not free:** a live version must decide after each reply.
- **Prompt-length medians and p90s** are over all calls of the 500 instances; costs are over the paired ones, which differ by at most one instance.
- **Qwen-tokenized, Qwen3.8 FLOPs constants** (as in step 18). The ratios are what carry over.

## The decision needed
Approval of step 19, and its push.

**On these released trajectories,** stubbing at age 1 with restore-on-need (V2r) is cheaper than raw and than masking:
- in FLOPs: 0.78–0.96× raw, against masking's 4.6–8.2×;
- in dollars at 10% and at 40% cache reads.

Its prompts are about 30–45% shorter than masking's.
