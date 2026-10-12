# Step 12c report: own turns, the free edit zone, and the slicing upper bound (2026-10-12)

**Branch:** `agentdb-12a`.
- 12b (`0a1ffd4`) was pushed after the secrets gate (`step12b-6-push.out`).
- 12c is one local commit, **not pushed**.

The analysis made no API calls and changed no behaviour. It covers the same 130 runs and 2,194 calls as 12b.
- **Files:** `runs/tier-a/step12c/` holds `summary.md` (all tables), `own_turns.csv`, `zone.csv`, `zone_skipped.csv` and `slices.csv`.
- **Scripts:** `runs/tier-a/scripts/step12c_analysis.py` and `step12c_summary.py`.
- **Weight of the rows:** only the step-9 arms have 30 runs; the 10d and step-11 rows are 1–2 runs each.

## 1. The model's own turns, prefilled again
Every token of an earlier reply that a later call computes (in U, beyond the block tail) is split by what it renders. Each token is charged C_token plus its prefill attention, C_attn·(position + ½); these sum exactly to the call's prefill attention. Shares are of the arm's total FLOPs.

| step 9 arm | own turns, all | visible text | write_file content | bash outside heredocs | heredoc bodies | other arguments | template glue |
|---|---|---|---|---|---|---|---|
| raw | 17.9% | 0.5% | 6.0% | 2.1% | 8.1% | 0.0% | 1.2% |
| econo+jev | 18.9% | 0.6% | 6.1% | 2.7% | 8.0% | 0.0% | 1.5% |
| clm-32k | 21.5% | 6.0% | – | 2.4% | 11.6% | – | 1.4% |
| clm-full | 18.3% | 5.1% | – | 2.7% | 9.2% | – | 1.3% |

- **File content the model writes is 14% of all FLOPs for our agent** (write_file 6%, heredocs 8%) **and 9–12% for CLM,** which has only bash, so all of it is heredocs.
- **Commands themselves (outside heredocs) are 2–3%.**
- **Visible text is about 0.5% for our agent,** whose replies are mostly bare tool calls, and **5–6% for CLM,** whose prompt asks for a THOUGHT section in every reply.
- **The 10d and step-11 rows look alike:** heredocs 8–16%. One run of 10d clm-32k has 18% visible text.

## 2. The free edit zone
For each call, the previous reply and the latest tool outputs were checked to lie in U.
- **Tool outputs: all 2,080 are wholly in U.**
- **Previous replies: 2,059 checked.**
  - **1,517 are wholly in U.**
  - **541 have their body in U, but some of the role header and empty think block in R.** Those tokens (`<|im_start|>assistant\n<think>\n…`) repeat the previous prompt's generation prompt, so they are cached whenever that prompt ended in a full 16-token block. Editing the reply's text or calls is still free.
  - **1 has a body token in R:** tc9 clm-32k scan-linux r1, call 17. CLM had reset its context to the same 4-message prefix it had at call 13, and the new reply begins with the same tool-call markup as call 13's reply. The cached span ends one token into the body (R 3,696; the body starts at token 3,695), on the `<tool_call>` markup; the arguments are in U.
- **134 calls have no previous reply:** each run's first call, plus the calls right after a summary rebuild.

**So the zone holds.** The previous reply's content and the latest tool outputs can be edited without breaking any cached prefix. The only exceptions are the template header tokens and that one coincidental markup token.

## 3. The slicing upper bound, per big output (1,000+ tokens; 238 outputs)
A chunk is 40 lines. A chunk counts as later used if one of these holds:
- **Quoted:** a later model turn contains an exact substring of 30+ characters of it that was new to the context.
- **Referenced by line number:** a later command on the same source path asks for a line the chunk shows numbered.
- **Referenced by symbol:** a new identifier from the chunk appears in a later command.

| step 9 arm | big outputs | tokens | later-used chunks | outputs with any used chunk | oracle slice saves |
|---|---|---|---|---|---|
| raw | 58 | 166,804 | 62 of 181 (34%) | 26 | 83,126 (49.8%) |
| econo+jev | 62 | 127,299 | 46 of 131 (35%) | 32 | 52,861 (41.5%) |
| clm-32k | 41 | 106,498 | 53 of 117 (45%) | 23 | 46,567 (43.7%) |
| clm-full | 52 | 134,617 | 53 of 136 (39%) | 28 | 66,691 (49.5%) |

- **Use comes through symbols (35–41 chunks per arm) and quotes (25–50).** Line numbers matter in only 0–6 chunks.
- **122 of the 238 big outputs have no later-used chunk at all.** For those the oracle keeps nothing. Compare 12b, where naming the file path counted as use: there, only 6–17% were never used.
- **The upper bound is therefore about 42–50% of big-output tokens in step 9.** The 10d and step-11 rows give 46–100%, from 3–7 outputs each.

**BM25 slices.** Chunks are ranked against the agent's last visible text before the output, and the top chunks are kept, in rank order, while they fit in the budget. Each cell gives tokens saved, then later-used chunks dropped, and the number of outputs where at least one was dropped:

| step 9 arm | used chunks | keep ≤ 25% | keep ≤ 50% | keep ≤ 75% |
|---|---|---|---|---|
| raw | 62 | 94.7%; 59 dropped (26 outputs) | 74.7%; 47 dropped (25) | 57.5%; 32 dropped (21) |
| econo+jev | 46 | 99.6%; 45 dropped (31) | 88.9%; 41 dropped (31) | 80.6%; 36 dropped (30) |
| clm-32k | 53 | 95.4%; 46 dropped (23) | 80.8%; 36 dropped (21) | 59.6%; 27 dropped (20) |
| clm-full | 53 | 96.3%; 50 dropped (28) | 82.0%; 41 dropped (25) | 58.2%; 28 dropped (23) |

- **BM25 drops most of the later-used chunks at every budget:** 51–98% of them, depending on the arm and the budget.
- **That is largely a granularity effect** (`step12c-3-granularity.out`). With 40-line chunks, a big output has a median of 2 chunks (mean 2.6; 52 have one chunk). A single chunk is then often over the budget, so the 25% slice kept no chunk at all in 85% of outputs; the 50% slice in 57%; the 75% slice in 39%.
- **The BM25 query** (the last non-empty reply text) did not exist for 39 of econo+jev's and 35 of raw's outputs. Our agent rarely writes visible text, so it fell back to the producing call's arguments. CLM's THOUGHT text served in most cases (10 and 15 fallbacks).
- **The test of BM25 is weak at this chunk size.** Smaller chunks (by tokens or by fewer lines) would make the 25–75% budgets meaningful. I have not run that, because it is not what was asked; it is your decision.

## Observations (not acted on)
- **File content the model writes (heredoc bodies and write_file content) is 9–14% of total FLOPs in step 9,** when its earlier replies are prefilled again. It sits in the free edit zone on the next call.
- **The free edit zone holds,** apart from the template header tokens and one coincidental token.
- **An oracle 40-line slice could save about 42–50% of big-output tokens.** Half of the big outputs are never quoted or referenced at chunk level.
- **BM25 at 40-line chunks is not a usable slice:** it either keeps nothing or drops most of what is later used.
