# Step 12d report: a fair slicer comparison (2026-10-12)

**Branch:** `agentdb-12a`.
- 12c (`a2613d7`) was pushed after the secrets gate (`step12c-5-push.out`).
- 12d is one local commit, **not pushed**.

**What was run.** There were no Purdue calls and no behaviour change. The only network calls were Jev's: 238 requests to TypeSafe, **0 failures**. They used 3,841,125 input tokens and 39,065 output tokens, and took 0.27 s per request (median). The key came from `.env` and was never printed. The answers hold probabilities and usage only, and are cached in `runs/tier-a/step12d/jev.jsonl`, so a rerun calls nothing.

**Files and scripts.**
- Script: `runs/tier-a/scripts/step12d_slicers.py`, in three phases: `prepare` and `evaluate` are offline; `jev` makes the TypeSafe calls.
- Tables: `runs/tier-a/step12d/summary.md`, overall and per arm.
- Per-output data: `slicers.csv`.

## Setup
- **The outputs:** the same 238 big outputs as 12c (1,000+ tokens as shown).
- **Chunks:** about 300 tokens each, cut at line boundaries: 2,275 chunks, a median of 7 per output (3–52). In 12c, 40-line chunks gave a median of 2. Five chunks are over 400 tokens, because a single long line becomes its own chunk.
- **Later-used chunks:** 12c's rules (quoted 30+ new characters; or a line number or new symbol in a later command). 677 chunks are later used, in 116 of the 238 outputs.
- **Slicers.** Each ranks the chunks. Chunks are kept in rank order, skipping any that would pass the budget (25%, 50% or 75% of the output's tokens):
  - **head + tail:** first, last, second, second-last, …;
  - **BM25 (reply):** against the last visible reply text plus the producing call's command and arguments;
  - **BM25 (task):** against the task text plus the last 3 commands;
  - **Jev:** its existing question (`Jev.arrival`, unchanged), on the logged conversation at that moment, with these chunks as its line ranges; ranked by Jev's probability;
  - **oracle:** the later-used chunks only.

## Tokens saved and recall (all 238 outputs)
Each cell gives tokens saved (as a share of the outputs' 577,174 tokens), then recall (the share of later-used chunks kept).

| slicer | keep ≤ 25% | keep ≤ 50% | keep ≤ 75% |
|---|---|---|---|
| head + tail | 78.7%; 20.8% | 55.1%; 45.6% | 30.6%; 70.8% |
| BM25 (reply) | 78.4%; 20.8% | 53.6%; 46.8% | 29.3%; 69.7% |
| BM25 (task) | 78.6%; 23.3% | 53.5%; 50.1% | 29.5%; **74.0%** |
| Jev | 78.6%; **27.8%** | 53.8%; **50.8%** | 29.7%; 72.8% |
| oracle (capped at the budget) | 88.2%; 39.0% | 78.5%; 70.2% | 72.9%; 87.6% |
| oracle, no budget | 69.2%; 100% | | |

- **The four real slicers are close.** Each budget fixes the tokens saved, and recall differs by at most 7 points.
  - **Jev is best at 25% and 50%,** by 4–7 points over head + tail.
  - **BM25 (task) is best at 75%.**
  - **BM25 (reply) is no better than head + tail.**
- **Recall is roughly proportional to the share kept, for every real slicer:** about 21–28% at 25%, 46–51% at 50% and 70–74% at 75%. That is about what a random choice of chunks would give, and well below the oracle at every budget.
- **Per arm (`summary.md`), the step-9 arms show the same picture.**
  - Jev leads on clm-full at 25% (31.5% recall, against head + tail's 17.1%).
  - Head + tail leads on raw at 75% (74.8%, against Jev's 69.4%).
  - The 10d and step-11 rows are too small to rank: 3–7 outputs, and 0–12 later-used chunks.

## FLOPs
The big outputs' FLOPs are computed by taking each big output out of every prompt in which it appears unchanged, shrinking P and R by its tokens, and recomputing Eq. 9 with the same G.
- This counts the prefill when the output is first computed, plus the attention in every later call, prefill and decode.
- **A check of this method** (`step12d-3-flops-check.out`): for the largest run of each step-9 arm, it agrees with a full rebuild (prompts re-tokenized and the cache simulated again) to within 0.13%.

**ASSUMPTION: a slice causes no extra calls.** A dropped later-used chunk is assumed never to be fetched again. This is optimistic for every slicer except the oracle.

| step 9 arm | PFLOPs | big outputs' share | head + tail 25 / 50 / 75% | Jev 25 / 50 / 75% | oracle, no budget |
|---|---|---|---|---|---|
| raw | 63.1 | 18.7% | 14.9 / 10.4 / 5.8% | 15.0 / 10.4 / 5.8% | 12.3% |
| econo+jev | 60.1 | 13.5% | 10.6 / 7.6 / 4.4% | 10.7 / 7.4 / 4.1% | 9.6% |
| clm-32k | 45.5 | 13.2% | 10.4 / 7.4 / 4.0% | 10.4 / 7.2 / 3.9% | 9.0% |
| clm-full | 49.2 | 16.8% | 13.4 / 9.5 / 5.3% | 13.3 / 9.2 / 5.1% | 11.9% |
| **all 130 runs** | 252.8 | 14.9% | 11.9 / 8.4 / 4.7% | 11.8 / 8.2 / 4.6% | 10.4% |

The two BM25 slicers save within 0.2 points of these at each budget (all runs: 11.8 / 8.2 / 4.5%). In the 10d and step-11 rows the big outputs are 9–17% of FLOPs, except step-11 clm-32k, which had no big output.

**At its best budget.** "Best budget" needs a rule, and I picked one: the budget that maximizes FLOPs saved × recall. **Please confirm or give the rule you meant;** the full grid is above.
- Under this rule the best budget is **50% for every real slicer.** They save 8.2–8.4% of all FLOPs while keeping 46–51% of later-used chunks.
- The oracle with no budget saves 10.4% while keeping all of them.
- Without a recall floor, the most FLOPs a real slicer saves is about 11.9%, at 25% (recall 21–28%).

## Observations (not acted on)
- **The ceiling.** Big outputs are about 15% of all FLOPs (13–19% in step 9). Perfect slicing (the oracle) recovers about 10% of all FLOPs.
- **No real slicer here beats keeping the head and the tail by much.** Jev's relevance answer adds 4–7 points of recall at 25–50%. That is far below the oracle's 39–70% at the same budgets.
- **The FLOPs numbers assume no extra calls.** At 50%, every real slicer drops about half the chunks later used. Any re-reads they cause would cost FLOPs this table does not count; measuring that needs live runs.
