# Step 15 report: where and when CLM's edits land (offline, 2026-10-13)

**Branch:** `agentdb-12a`.
- 14b was approved and pushed after the secrets gate (`446997c`; `step15-0-push.out`).
- Step 15 is one local commit, **not pushed**.

The step made no model calls and no change to CLM, the harness or EconoContext. CLM is zero-shot as released, on standard prefix caching.
- **Scripts:** `runs/tier-a/scripts/step15_placement.py` and `step15_summary.py`.
- **Tables:** `runs/tier-a/step15/summary.md`, including per-arm TBLite blocks.
- **Data:** `runs.csv` and `edits.csv` (numbers and ids only).

**Everything here is a fixed-trajectory estimate on the canonical track.** Plans (b)–(d) change the order or timing of the tokens CLM's model would have seen; the replies and the content of every edit are the logged ones.

## The answer
**No: a cost-planned materializer does not beat the best simple rule by a margin worth building.**
- Its edge over the simple tail rule is **0.9% of CLM's FLOPs on TBLite** and **2.8% on BrowseComp-Plus**.
- On BrowseComp-Plus the edge is 1.8% against the even simpler "rollback notices at the end".
- The simple rule itself saves **4.7%** (TBLite) and **17.0%** (BrowseComp-Plus).

Almost all of the BrowseComp-Plus saving comes from one placement: CLM's rollback notice at position 2.

| share of plan (a)'s FLOPs | TBLite (63 runs, 33 edits) | BrowseComp-Plus pilot (8 runs, 87 edits, 30 of them rollbacks) |
|---|---|---|
| (a) as executed | 100.0% (100.7 PFLOPs) | 100.0% (106.6 PFLOPs) |
| (a′) in place, CLM's re-serialization not replayed | 100.0% | 100.1% |
| **(b) tail** | **95.3%** | **83.0%** |
| ↳ tail at rollbacks only, in place elsewhere | (no rollbacks) | **82.0%** |
| ↳ tail at context edits only | 95.3% | 100.6% |
| (c) batched | 96.4% | 88.0% |
| **(d) cost-planned** | **94.4%** | **80.2%** |
| **gap: best simple rule → (d)** | **0.9%** (2 of 63 runs give 80% of it) | **2.8%** (1.8% from the rollbacks-only rule; 3 of 8 runs give 80%) |

**How close (d) is to the optimum:**
- On all 24 runs with 1–6 edits, (d) equals the exhaustive optimum over all 3ⁿ plans.
- On the 6 longer BrowseComp-Plus runs, 20 seeded random restarts of the descent never beat it.

So the small gap is not a weak planner. It is an upper bound: (d) knows the future.

**How well the piece model reproduces the real runs:** plan (a) in the piece model reproduces the real canonical track to within 1.4% of prompt tokens and 0.8% of FLOPs, on both benchmarks.

## 1. CLM's budget rollback, from the code (`baselines/clm/upstream`, pinned `648a48b`)

**What triggers it.** Before every call, `BudgetController.evaluate` (`clm/clm_harness/utils/budget.py:171`) counts the prompt as tiktoken × a learned `tok_ratio`. It rolls back when all of these hold:
- the count is over `strict_target = budget − reserve` (`budget.py:83`; 23,560 − 2,048 = 21,512 on BrowseComp-Plus);
- retries remain (`max_num_retry_on_limit`, which the harness sets to 50 by default, `clm_agent/harness.py:207`);
- something beyond the protected prefix can be dropped (`budget.py:179–192`).

Otherwise it gives one final turn, then stops.

**What it removes.** `rollback_to_margin` (`budget.py:287`) "Drop[s] the NEWEST turns (in place) until the retained context leaves `margin` free tokens under the strict target":
- it never touches the protected prefix, and never leaves a tool call without its result;
- the margin is `retry_edit_margin` (2,048), and grows only after 3 consecutive ignored rollbacks (`budget.py:273–285`);
- the dropped turns are gone for good (`harness.py:528–534`).

**Where the notice goes.** `_note_rollback` (`harness.py:419–449`) writes `rollback_message` (`budget.py:310`):
- the text begins "[SYSTEM NOTICE — CONTEXT LIMIT HIT (retry k/50)] … ROLLED BACK … CONDENSE YOUR CONTEXT THIS TURN", and it lists up to 5 rolled-back commands;
- **on the first rollback it is inserted at index `_protect` = 2**, right after the system prompt and task (`_PROTECT = 2`, `harness.py:106`). `_protect` then grows by one, so the notice becomes part of the protected prefix;
- **every later rollback rewrites that message in place** (`harness.py:446–448`) with a new retry count, turns lost, current size and command list.

**Why it goes there.** The docstring says: "A rollback also erases the model's memory of what it just tried, so without a durable trace it re-issues the same command, blows the budget again and livelocks. The ledger survives every rollback and every context edit."
- Pinning it in the protected prefix is what makes it survive.
- The cost side effect: its text changes every time, so each rollback changes the prompt at about 1,590 tokens in (the median on BrowseComp-Plus), and the next call re-prefills everything retained.

## 2. Method (as you specified, with what I had to decide)

**The same logical context.** CLM edits its context as text. After an edit, every later turn comes back re-serialized (`context_utils/context_string.py`):
- `parse_back` folds tool outputs into user text and merges same-role turns;
- `rendered_text` writes each assistant turn as "reasoning, content, name args".

So I replay **pieces**, not messages. Each logged request is decomposed, by exact matching, into:
- tool outputs, assistant contents and commands;
- reasoning paragraphs;
- other text of 30+ characters (task, nudges, notices, summaries);
- glue and 5-token message wrappers.

A piece's identity is its text. Tool-call ids repeat across turns in CLM's requests, so they cannot serve as identities.

**Plans.**
- **An edit** is any call whose piece sequence is not the previous one plus pieces appended at the end.
- **(b) tail:** at an edit, new pieces (notices, summaries, reasoning made visible) go at the end, and removals happen in place.
- **(c) batched:** as (b), but removals are deferred until the next prompt would pass the limit.
  - The limit is 21,512 on BrowseComp-Plus and 29,952 for clm-32k. For clm-full it is 65,536 minus the request's max_tokens.
  - The check uses the piece count, scaled to the real count of that call.
- **(d) cost-planned:** at each edit, chooses in place, tail or deferred.
  - Each option is priced by Eq. 9 over the rest of the run (a greedy rollout under each simple rule, keeping the best), then refined by coordinate descent.
- **My choice: formatting follows content.** In (a′)–(d), a retained piece keeps its old wrapper and glue; CLM's re-serialization of retained turns is not replayed.
  - (a) − (a′) is that re-serialization's own cost: about 0%.

**Pricing.**
- Eq. 9 per call, with the logged G.
- A vLLM-style prefix cache at piece granularity: 16-token rounding and the last-token rule.

## 3. Where and when, per benchmark

**BrowseComp-Plus: where.** Moving the rollback notice to the end saves **18.0%** of CLM's FLOPs on its own.
- Under the tail rule the notice lives near the end, so rewriting it breaks almost nothing; the rollback's own removals are the newest turns, which also sit at the end.
- (d) puts 28 of 30 rollback notices at the tail.
- For the context edits (summaries), placement barely matters: tail alone gives 100.6%, and (d) keeps 39 of 57 in place. A summary replaces the turns at the same spot where it lands: the median gap between its first insertion and its first removal is 5 tokens.

**TBLite: where.** There are no rollbacks; the 4.7% comes from the context edits.
- In 16 of 33 edits, CLM's new text (summaries and reasoning) lands 1,000+ tokens before its first removal: median first insertion at 3,252 tokens, first removal at 3,694.
- The gain is concentrated: clm-full 92.1%, clm-32k 98.3%. 2 runs give 80% of the gap to (d).

**When (batching) does not pay.**
- (c) is worse than (b) on both benchmarks: 96.4% vs 95.3% on TBLite, and 88.0% vs 83.0% on BrowseComp-Plus.
- Deferred removals keep a lot of logically removed text visible:
  - BrowseComp-Plus: 32.4% of calls, about **8,000 extra tokens** each;
  - TBLite: 13.2% of calls, about 4,000 each.
- Every later call then pays attention over those tokens, and the model would read text CLM meant to remove.
- (d) defers only 7 of 33 edits on TBLite and 4 of 87 on BrowseComp-Plus.

## 4. Reasoning made visible

When CLM rebuilds its context, each assistant turn's earlier reasoning comes back as message text (`rendered_text` puts `reasoning_content` first). Our gateway had stripped it from every request.

Its cost is F(a) minus F(a with those pieces removed from every prompt):

| | TBLite | BrowseComp-Plus |
|---|---|---|
| share of CLM's FLOPs | **3.3%** | **8.1%** |
| runs where it occurs | 19 of 63 (every run with an edit has one, except 3) | 6 of 8 |
| tokens made visible | 52,595 | 58,376 |
| concentration | 6 of 63 runs give 80%; the largest run is 0.8% of the benchmark's FLOPs | 3 of 8 runs give 80%; **question 986 alone is 5.0%** (27.5% of that run, the edit behind its 34,393-token prompt) |

**So it is not rare: it happens in nearly every run that edits, but a few runs carry most of the cost.**

Separated from placement: without these pieces, the plan shares barely move.
- TBLite: (b) 95.1%, (d) 94.1%, gap 1.0%.
- BrowseComp-Plus: (b) 83.9%, (d) 81.8%, gap 2.0%.

So where the reasoning lands is not what drives the placement result.

## Caveats
- **These are fixed-trajectory estimates.** Under (b)–(d), the model would see notices and summaries at the end, and under (c) removed text for longer, and it might act differently. The trajectories were not re-run.
- **The pilot is small:** 8 BrowseComp-Plus runs, and 3 of them give 80% of the gap.
- **The piece model approximates the template's wrappers.** Plan (a) is checked against the real canonical track: 98.6–98.7% of prompt tokens and 99.2% of FLOPs. All plan shares are relative to the piece model's (a).

## The decision needed
Approval of step 15, and its push. On this evidence a cost-planned materializer adds about 1–3% of FLOPs over "append new text at the end". The large, simple win in CLM's cost on BrowseComp-Plus is where its rollback notice sits.
