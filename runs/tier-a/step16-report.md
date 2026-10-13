# Step 16 report: how much of CLM's FLOPs is removable waste, and what kind (offline, 2026-10-13)

**Branch:** `agentdb-12a`.
- Step 15 was approved and pushed after the secrets gate (`471af5d`; `step16-0-push.out`).
- Step 16 is one local commit, **not pushed**.

The step made no model calls and no code changes outside the analysis scripts.
- **Scripts:** `runs/tier-a/scripts/step16_waste.py` and `step16_summary.py`.
- **Tables:** `runs/tier-a/step16/summary.md`.
- **Data:** `runs.csv` and `cases.csv`: numbers, ids and call numbers only. BrowseComp-Plus answers were matched locally in `~/econo/bcp`.

**Everything here is a fixed-trajectory estimate on the canonical track**, priced on step 15's piece model: within 1.4% of the real prompt tokens.
- **How each category is priced:** F(run) − F(the same run without the wasted turns or pieces).
- **The overlap-free total:** removes the union of the categories at once.

## Waste as a share of CLM's total FLOPs

| category | TBLite: CLM (63 runs) | BrowseComp-Plus: CLM (8 runs) | TBLite: our harness (34) | BrowseComp-Plus: our base (4) |
|---|---|---|---|---|
| (a) repeated actions, identical result | 0.0% | 0.0% | 0.2% | 0.0% |
| ↳ result removed by CLM itself (context manager) | 0.0% | 0.0% | – | – |
| ↳ result still visible (model behaviour) | 0.0% | 0.0% | 0.2% | 0.0% |
| (b) work redone after a rollback (context manager) | 0.0% (no rollbacks) | 0.0% | – | – |
| (c) turns after sufficiency | 0.2% (conservative) | **10.5%** | 0.2% | 11.0% |
| ↳ answer removed by CLM's own edit (context manager) | – | **8.4%** (one run) | – | – |
| ↳ answer still visible (model behaviour) | 0.2% | 2.1% | 0.2% | 11.0% |
| (d) runs that ended without an answer | 0.0% | 0.0% | 22.4% | 80.1% |
| (e) unreferenced observations | 2.2% | **7.5%** | 2.5% | 31.8% |
| **overlap-free total, (a)–(e)** | **2.3%** | **17.8%** | 24.7% | 91.2% |
| (f) not in your list: replies cut at the output cap with only thinking | 1.1% | **21.6%** | 9.8% | 0.0% |
| **overlap-free total, (a)–(f)** | 3.4% | **39.1%** | 33.1% | 91.2% |

**Is there a waste pool above 20% that a context manager could plausibly address?**
- **Not among the audited categories:**
  - What the context manager could address totals about 16% on BrowseComp-Plus: the one run where CLM's own edit discarded the answer (8.4%) and unreferenced observations (7.5%). On TBLite it is about 2%.
  - The one audited pool above 20%, thinking-only replies cut at the output cap (21.6%), is model and harness behaviour.
- **Outside the audit, there is one:** CLM's own re-prefill after its edits and rollbacks, which is 21.3% of its BrowseComp-Plus FLOPs. Step 15 found up to about 20% of the total removable by placement alone.

## 1. Waterfall (parts sum to 100% of each group's FLOPs)

| part | TBLite: CLM | BrowseComp-Plus: CLM | TBLite: ours | BrowseComp-Plus: base |
|---|---|---|---|---|
| generation, task | 33.2% | 14.9% | 38.8% | 13.7% |
| generation, context management | 1.2% | 2.6% | – | – |
| generation, mixed | 0.0% | 0.0% | – | – |
| generation, no command | 1.0% | **13.4%** | 9.1% | 3.1% |
| new observations: search results / documents / command outputs | 20.9% (outputs) | **23.3%** / 1.3% | 19.5% | 67.1% / 2.0% |
| new observations: management outputs | 0.2% | 0.9% | – | – |
| **re-prefill after edits and rollbacks** | 7.6% | **21.3%** | – | – |
| **reasoning made visible** | 2.6% | 6.8% | – | – |
| text written by edits (summaries, notices) | 0.6% | 0.9% | – | – |
| own turns and harness messages | 14.7% | 5.7% | 15.1% | 1.0% |
| system and task (first call) | 8.4% | 0.6% | 4.1% | 3.3% |
| cache rounding | 0.3% | 0.1% | 0.3% | 0.1% |
| attention (prefill + generation) | 9.1% | 8.3% | 13.1% | 9.7% |

On BrowseComp-Plus, CLM spends:
- **32.5%** of its FLOPs on its context handling: re-prefill 21.3%, reasoning made visible 6.8%, management generation 2.6%, management outputs 0.9%, and edit-written text 0.9%;
- **24.6%** on new search results and documents;
- **13.4%** on calls that produce no command.

## 2. The categories

**(a) Repeated actions.** A repeat is the same normalized command, search query or document id run again.
- **I counted only repeats with a byte-identical result body (my choice).** A re-run whose result changed brought new information (e.g. tests after an edit).
- CLM: TBLite 2 repeats, 1 identical, still visible; BrowseComp-Plus 2 repeats, both with a changed result.
- **Neither benchmark shows any repeat after CLM removed the result.** CLM's agent does not lose results and fetch them again.

**(b) Redone after a rollback.** Across 30 rollbacks, **no exact redo** of a dropped command occurs within 5 calls, although those calls hold 84 searches.
- The model rephrases instead, as the rollback notice asks.
- As a sensitivity check (not in the total), rephrased repeats (same action type, word overlap ≥ 0.6) number 5 calls and cost 2.6%.

**(a) and (b) together, by who could prevent them:**
- **context manager** (the result was removed by CLM): 0%;
- **model behaviour** (the result was still visible): 0.0% on CLM, 0.2% on our TBLite runs.

**(c) Turns after sufficiency.** Of 8 BrowseComp-Plus runs, 3 have the gold answer in a prompt; the other 5 never retrieved it.
- **Run 1226 (8.4% of CLM's FLOPs):**
  - The answer reached the prompt at call 1, and the model mentioned it in its reasoning.
  - At call 2, near its limit (19,468 of 21,512 tokens), CLM's model ran a context edit that removed both search results (17,830 tokens). Its summary did not keep the answer.
  - The answer never returned. The run used 28 more calls and ended at the budget with a wrong answer.
  - **This waste came from the context manager**, not from ignoring visible evidence.
- **Runs 600 and 741 (2.1%):** the answer stayed visible and the model took 5 and 3 more calls before answering (both correct).
- **TBLite:** passing runs only, with a conservative rule: any command that is not clearly read-only counts as a state change, including test runs. CLM submits within one call of its last state change in 54 of 57 runs, leaving 3 extra calls (0.2%).

**(d) Runs that ended without an answer.**
- **CLM:** none on either benchmark; all 8 BrowseComp-Plus runs wrote an answer file. One TBLite CLM run crashed (`RuntimeError`); a crash is neither the limit nor the budget, so it is excluded.
- **Contrast:**
  - our TBLite raw arm has 2 runs that overflowed the 65,536-token window (22.4%);
  - BrowseComp-Plus base stopped at the budget without an answer in 3 of 4 runs (80.1%).

**(e) Unreferenced observations.** An output counts as unreferenced when no later reply quotes 30+ characters of it or names something distinctive from it.
- **Distinctive names:** paths, code-like identifiers, docids and multi-word proper names.
- **My change:** I narrowed 13b's rule, which counted any 8-letter word as a name.

| | outputs unreferenced | share of observation tokens | share of FLOPs |
|---|---|---|---|
| CLM, BrowseComp-Plus | 123 of 323 | 18.7% | 7.5% |
| CLM, TBLite | 239 of 840 | 8.1% | 2.2% |

On BrowseComp-Plus, per document inside a search result, **51.6% of snippet tokens are never referenced.** A whole result is rarely useless; half of each one is.

**(f) Not in your list: replies cut at the output cap.**
- **What happened:**
  - **68 CLM calls on BrowseComp-Plus** hit the 4,096-token output cap with only thinking: no content, no command.
  - That is 278,528 generated tokens, which our gateway strips, so the model never sees them.
- **Concentration:** 3 runs of 100 turns (986, 1081, 333) give 19.0 of the 21.6%.
- **Position:** of the 69 cut-off replies without a command (including one with content), 21 come right at an edit or rollback, and 13 follow another cut-off reply.
- **Contrast:** our harness has 9.8% on TBLite (36 calls: 9 at tc9's 8,192 cap, 27 at 2,048) and none on BrowseComp-Plus.

## Caveats
- **These are fixed-trajectory estimates.** Removing a turn assumes the run would otherwise proceed unchanged.
- **The pilot is 8 runs.** On BrowseComp-Plus, (c) is mostly one run and (f) mostly three.
- **The gold-answer match is exact after the judge's normalization.**
  - A paraphrased answer is missed, so (c) is a lower bound.
  - A short answer could match by chance: in 1226 it is two words, and the model's reply at call 1 mentions it.
- **The TBLite state-change rule is conservative,** so (c) there is a lower bound.
- **"Referenced later" uses quotes and names:** a paraphrase without a distinctive name counts as unreferenced, which inflates (e). As a counterweight, ordinary words never count.

## The decision needed
Approval of step 16, and its push.

The audit finds no 20%+ waste pool that a context manager owns, beyond its own re-prefill (21.3%; step 15).
- CLM's remaining large waste is in the model: thinking-only replies cut at the cap (21.6%).
- The one costly context-manager failure is a single edit that discarded the answer. It is a reminder that a manager's removals can cost more than they save.
