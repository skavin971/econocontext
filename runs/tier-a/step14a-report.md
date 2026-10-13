# Step 14a report: the pilot finished, the pricing fixed, recomputed (2026-10-13)

**Branch:** `agentdb-12a`.
- **Commits:** `edad515` and `61b6cf7` are still unpushed, and so is 14a's own commit.
- **Changes:** none to EconoContext, the harness or the baselines in 14a. `measure/flops.py` pricing goes back to the canonical rebuild; `benchmarks/bcp/run.py` gains a summarize-only option; analysis scripts. The tokenizer fix, the split-only rule and the budget margin came in step 13 and stay.
- **Tests:** 151 pass.

**Everything below is computed on fixed trajectories.** The canonical track prices a counterfactual serialization of each fixed trajectory, not executed FLOPs.

## 1. The pilot (BrowseComp-Plus, development split)
- **Base:** finished the question running when your message came (986), then stopped. **4 questions** in total (591, 570, 741, 986).
- **CLM:** continued until **8 development questions**: 591, 570, 741, 986, 333, 1226, 600 and 1081.
  - The arm order was unchanged, and no other arm was started.
  - Every `context_snapshots` folder and the full gateway logs are kept, in `~/econo/bcp/runs/bcp13p/` and `~/econo/bcp/gateway/`.
- **How the runner was stopped:** at that point CLM was running 986, so I stopped only the runner loop. The trial itself was left running and kept awake. When it ended, its summary was written with the new `--summarize-only`; base then ran on 986, and CLM alone on the next four questions (`runs/tier-a/scripts/step14a_finish_pilot.sh`, `step14a-1-pilot.out`).
- **Wall time:** **CLM passed 40 minutes on 1081 (43.1 minutes)**, and it was let finish. 333 took 37.1 minutes and 986 took 33.7.

**Per arm** (`step14a-4-pilot-report.out`). Accuracy over 8 questions is a sanity check, not a result.

| arm | questions | judged correct (exact match) | answer files | calls / q | searches / q | reads / q | wall time / q | PFLOPs / q, canonical | observed | peak prompt | how runs ended |
|---|---|---|---|---|---|---|---|---|---|---|---|
| base | 4 | 1 (1) | 1/4 | 5.0 | 3.8 | 0.2 | 1.9 min | **1.59** | 3.11 | 24,839 | at the budget: 4; three without an answer |
| clm | 8 | 2 (2) | 8/8 | 64.1 | 37.5 | 2.2 | 21.0 min | **13.43** | 21.21 | 34,393 | submitted: 5 (three of them at the 100-turn limit, after CLM's finalize nudges); at the budget: 3 |

**Validation:**
- every call validated (base 20/20, CLM 513/513);
- no split-only differences remain after step 13's tokenizer fix;
- the largest absolute ΔP is 3 tokens, all in the tools block.

**CLM edits and rollbacks:** 7.0 edits and 3.8 rollbacks per question.

**CLM went over its budget once:** question 986, call 75, sent a 34,393-token prompt, over the 21,512-token limit. All its other calls fit. CLM's budget control uses tiktoken scaled by a learned ratio.

**Projection:**
- 150 × 3 × 3, at the pilot's mean of 44 calls and 15 minutes per run, comes to about 61,000 requests: about **7.7 days** at the gateway's 8,000 requests a day.
- Run one at a time, the trials alone take about 14 days.
- Both figures are from 12 runs.

## 2. The pricing fix: two tracks
- **Canonical (the headline for comparing arms):** Eq. 9 and the cache simulation on our rebuild of each logged request, with the tools in the order the client sent them. This prices a counterfactual serialization of a fixed trajectory, not executed FLOPs.
- **Observed:** the same on Purdue's `return_token_ids`: what Purdue actually computed, including its tools-block key reordering. In the code it is `F_server`, and every report shows it next to the canonical figure.
- **Validation is unchanged:**
  - the server's IDs against our rebuild;
  - split-only differences accepted;
  - BrowseComp-Plus's budget check keeps the max(0.5%, 64 tokens) margin.

## 3. Did any online decision use FLOPs or the cache simulation? No.
I read every component that ran in the logged runs:

| component | what its decisions use |
|---|---|
| EconoContext's Owner (`econocontext/owner.py`; and the earlier SQLite Owner in tc9) | prices from the billing card (`pricing/lifecycle.py`), token counts (`count_text`), and the server's `prompt_tokens` for its prompt size; no FLOPs, no cache simulation |
| `Econo` policy (`econocontext/policy.py`) | wraps the Owner |
| Base's budget gate and the final-turn rule (`harness/budget.py`) | `count_prompt` token counts |
| Summary's 75% trigger (`baselines/summary/policy.py`) | `count_prompt` |
| CLM (`clm_agent/harness.py`, `utils/budget.py`) | its budget decisions use a tiktoken count × a learned ratio; its FLOPs (`export_ctx_trajectory`) are computed once, after the run has ended, and only written to a file |
| the runners (`benchmarks/tblite/run.py check`, `benchmarks/bcp/run.py`) | gates on invalid-call counts, crashes and Jev fallbacks; FLOPs only appear in progress lines and summaries |
| the gateway | no pricing at all |

No run needs listing.

## 4. Recomputed on both tracks: what changed

**13b TBLite (forced eviction).**
- **The replay's policy numbers are identical before and after the fix** (`step14a-2-tblite-replay.out` against `step13b-2-tblite-forced.out`): the replay always rebuilt its prompts canonically. Replayed prompts exist only canonically: they were never sent, so they have no observed track.
- **The results (31 runs pass the limit):**
  - oldest first: **+10.7%** FLOPs against no eviction, 15/31 runs reach their answer;
  - cheapest (least text after the item): **−5.0%**, 15/31;
  - knowing: **−8.3%**, 14/31;
  - base's rule (stop at the limit): −38.6%, **0/31**.
- **The gaps:**
  - **oldest → cheapest: 15.7%** of the no-eviction FLOPs;
  - **cheapest → knowing: 3.3%** (and one fewer run reaching its answer).

**What base and summary actually did, on both tracks:**

| run | canonical PFLOPs | observed PFLOPs |
|---|---|---|
| raw-32k, bandit | 5.42 | 27.80 |
| base-32k, bandit | 2.16 | 8.79 |
| summary-32k, bandit (mean of 2) | 8.59 | 32.85 |
| base-32k, scan-linux | 2.95 | 13.78 |

**All 130 TBLite runs** (`step13-pricing-check.out`): observed is **3.63× canonical overall**.
- Our harness's arms (raw, econo+jev, base, summary): 3.8–5.1×.
- CLM's arms: 1.4–2.1×.
- **This is the conclusion that differs between tracks.** On the observed track, Purdue's key reordering costs our 4-tool harness far more than CLM's single-tool requests, so any cross-arm FLOPs comparison would mostly measure Purdue's front end. On the canonical track, no earlier conclusion flips.

**Earlier numbers that changed:**
- **The pilot's progress lines:** those written while the headline was briefly on server IDs were observed numbers. In canonical PFLOPs:
  - CLM 570: 32.08 → **18.70**;
  - base 570: 3.69 → **1.71**;
  - base 741: 2.98 → **1.27**;
  - CLM 741: 3.73 → **1.92**.

  Question 1's figures in the step-13 report (base 1.84, CLM 15.09) were already canonical, and are unchanged.
- **The tokenizer fix:**
  - no TBLite number changes: 2,194 of 2,194 calls were already identical;
  - pilot question 1's validation goes from 55/65 to 65/65.
- **Steps 12b–12g:** all canonical, and unchanged.

## 5. The BrowseComp-Plus part of 13b is dropped
14b replaces it. The script now refuses that mode.

## The decision needed
Approval of 14a, and push (with `edad515` and `61b6cf7`). 14b follows your amendment: E/N/V/R?/P with real reference costs, a stateful replay with repairs and an unsupported share, potentially stale current-state evidence, CLM's management cost as an upper bound, and the three headline numbers on the canonical track.
