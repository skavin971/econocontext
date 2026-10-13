# Step 18 report: The Complexity Trap's data, replayed with our engine (offline, 2026-10-13)

**Branch:** `agentdb-12a`.
- Steps 16 and 17 were approved and pushed after the secrets gate (`5e4f968`, `4cb3907`; `step18-0-push.out`).
- Step 18 is one local commit, **not pushed**.

The step made no model calls and no code changes outside the analysis scripts.
- **Scripts:** `runs/tier-a/scripts/step18_complexity_trap.py`, `step18_summary.py` and `step18_gemini_cost_check.py`.
- **Tables:** `runs/tier-a/step18/summary.md`.
- **Data:** `instances.csv` (instance ids and numbers only).

**Everything here is a fixed-trajectory estimate on the canonical track.**

## The answer
**Their "masking is ~52% cheaper" holds only for the price they used. It reverses in FLOPs, and at a cheaper cache-read price.**

Qwen3-Coder-480B, SWE-agent, SWE-bench Verified; 499 instances paired across the three arms (one raw file is unreadable):

| metric | raw | masking | summary | **masking vs raw** | summary vs raw |
|---|---|---|---|---|---|
| their reported instance cost | $642.94 | $304.90 | $318.27 | **−52.6%** | −50.5% |
| tokens sent (Qwen) | 979.3M | 413.2M | 633.0M | **−57.8%** | −35.4% |
| dollars, no cache-read discount | $1,693 | $450 | $698 | **−73.4%** | −58.8% |
| dollars, their cache-read price (40% of input) | $705 | $321 | $326 | **−54.5%** | −53.8% |
| dollars, cache read at 10% of input | $210 | $256 | $139 | **+21.5%** | −33.8% |
| FLOPs (Eq. 9) | 1,725 PF | 11,489 PF | 3,572 PF | **+566%** (6.7×) | +107% |
| new-prefill tokens | 26.0M | 214.1M | 64.3M | +723% | +147% |
| agent turns | 29,252 | 30,515 | 35,039 | +4.3% | +19.8% |

- **The claim reproduces:** −52.6% from their own per-trajectory costs, against 52.7% in their released table.
- **On our tokenization:** −54.5% at their price card, −57.8% in tokens sent, −73.4% without any cache discount.
- **Below a cache-read price of about 13% of input, masking costs more than raw.** At 10% it is +21.5%.
  - Dollars are linear in the cache-read price c: raw $45.7 + $1,647.5·c; masking $234.0 + $216.4·c. They are equal at c ≈ 0.13.
- **In FLOPs, masking costs 6.7× the raw agent.** It is cheaper on only 5 of 499 instances.
- **The cause is the prefix cache:**
  - Masking the 11th-newest observation on every turn breaks the prefix about 10 turns back.
  - The cached share of input drops from 97% (raw) to 48%. Their own estimates agree: 97.8% and 48.0%.
  - Every call re-prefills the last ten turns: 8.2× the new prefill.
- **The trajectories are not the cause.** Applying their masking rule to the raw arm's own prompts gives:
  - tokens sent −60.8%;
  - dollars at 40% −58.2%, at 10% +12.0%;
  - FLOPs **+520%**, with masking more expensive on all 499 instances.
- **LLM-Summary** breaks the prefix only when it summarizes (its cached share is 90%). It costs about 2× the raw agent's FLOPs and is cheaper than masking in FLOPs and at a 10% cache-read price.

## 1. What they released
**Dataset:** `JetBrains-Research/the-complexity-trap` on HuggingFace, **Apache-2.0**, 35 files, **22.5 GB** of `.tar.gz` archives.
- **SWE-agent: 17 archives,** 0.06–0.82 GB each, 500 SWE-bench Verified instances per run, raw / masking / summary each:
  - Gemini 2.5 Flash, with and without thinking;
  - Qwen3-32B, with and without thinking;
  - Qwen3-Coder-480B;
  - plus 2 combined runs on 50 instances.
- **OpenHands: 14 archives** (Gemini 2.5 Flash): masking at 9 values of M, 4 full runs of **2.2–3.8 GB**, and 50-instance subsets of 0.24–0.39 GB.
  - **I downloaded no OpenHands archive.** Their plots use a cleaned CSV in `auxiliary-data` instead.

**Code:** `JetBrains-Research/the-complexity-trap` on GitHub, **MIT**, a fork of SWE-agent; I read commit `bf15b5f`.

**Full per-turn trajectories: yes, for SWE-agent.** Each `.traj` holds, per turn:
- `messages`: the full prompt as sent, after the history processor, with masked observations as "Old environment output: (N lines omitted)";
- `response`, `thought`, `action`, `observation`;
- `turn_statistics`: their `raw_input`, `cached_input` and `output` tokens, and the cost.

The summary arm also has `summaries`: each summarizer call's prompt (`context`) and its output.

**Downloaded** (each under 1 GB, 2.35 GB in all) to `~/econo/ctrap/data/`, streamed and never extracted (14 GB uncompressed, mostly logs):
- the three Qwen3-Coder archives;
- the Gemini masking archive, for the check in section 2.

## 2. How their "instance cost" is computed

**API models (Gemini).**
- `instance_cost` is the sum of `litellm.cost_calculator.completion_cost(response)` per call: agent calls at `sweagent/agent/models.py:901`, summarizer calls at `:1022`.
- litellm `~=1.77`, card for gemini-2.5-flash: input $0.30, output $2.50, **cache read $0.075 per million tokens** (25% of input).
- The cache price applies only to cached tokens the provider reports. The released logs do not contain the responses, so I checked from the costs (`step18-3-gemini-cost-check.out`):
  - over the masking run's 21,839 turns, the logged cost is **$88.51**;
  - their own tokens priced without a discount come to **$80.63**, and with litellm's cache price **$49.40**;
  - in the most common bin, 90–100% cached, the logged cost is exactly the no-discount price (ratio **1.000**). A discount would give about 0.25–0.3.
  - **So no cache-read discount reached their Gemini costs.**

**Local models (Qwen).**
- Cost is 0 at run time (`is_local_model`). It is recomputed afterwards in `notebooks/posthoc_compute_qwen_costs.py` from each turn's statistics: `raw_input × input + cached_input × cached + output × output`, plus `internal_reasoning × output`.
- **Qwen3-Coder** (the ~52% model), $ per million tokens, tiered by context:

  | context | input | cached input | output |
  |---|---|---|---|
  | under 32k | $1 | **$0.40** | $5 |
  | 32k–128k | $1.8 | $0.72 | $9 |
  | 128k–256k | $3 | $1.2 | $15 |
  | 256k–1M | $6 | $2.4 | $60 |

  Cached input is **a 40% discount**.
- **Qwen3-32B:** input $0.70, **cached $0.70 (no discount)**, output $2.80 ($8.40 with thinking).

**`cached_input` is their own estimate, not a server count.** It is the longest message-level common prefix with the *previous* prompt (`_calculate_cached_tokens`, `models.py:301`), counted with litellm's `token_counter`.
- Our prefix-cache simulation, over all earlier prompts, gives nearly the same shares: raw 97.3% vs 97.8%, masking 48.2% vs 48.0%, summary 89.8% vs 90.4%.

**The "~52%":** Qwen3-Coder's masking arm costs $0.610 per instance against $1.288 for raw (`auxiliary-data/bootstrapped_cis.csv`).

## 3. Method
**Engine:** every call as sent is priced, the summarizer's calls included.
- Each summarizer call is placed just before the first agent turn whose prompt contains its summary.
- Each distinct message (content plus tool-call names and arguments) is tokenized once with our Qwen tokenizer, plus a 5-token wrapper; the prompt adds a 3-token generation prompt. G is the response or summary in Qwen tokens.
- A prefix cache per instance at message granularity (16-token rounding, last-token rule; steps 15–17) gives R.
- **Our counts are 108.8% of theirs**, from a different tokenizer and our wrappers.

**Prices:**
- tokens sent;
- dollars without discount;
- dollars at their card (tiered by context, cached at 40%), and the same card with cache reads at 10%;
- Eq. 9 FLOPs with our Qwen constants.

**Counterfactual:** SWE-agent's `LastNObservations` (n = 10, polling 1) applied to the raw arm's prompts. Every observation except the first (the task) and the last 10 becomes "Old environment output: (N lines omitted)".

## Caveats
- **Different models and tokenizers.** These are Qwen3-Coder trajectories priced with Qwen3.8's tokenizer and FLOPs constants. Ratios between arms are what matter, and our token counts are within 9% of theirs.
- **The FLOPs assume a server with prefix caching.** Their local Qwen runs were served by vLLM, whose caching settings are not in the released data.
- **Arms are different runs,** so behaviour also differs (masking makes 4% more turns). The same-trajectory counterfactual removes that, and gives the same answer.
- **The Gemini check is indirect**, because the provider responses are not in the logs.
- **Other models and OpenHands were not replayed.**

## The decision needed
Approval of step 18, and its push.

**On their own released data,** observation masking saves dollars only when a cache read costs more than about 13% of the input price. Their Gemini costs had no discount, and their Qwen3-Coder card a 40% one. Under prefix caching it costs about 6–7× the raw agent's FLOPs.
