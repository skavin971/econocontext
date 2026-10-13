# Step 14b report: re-reads, versions and CLM's management cost (2026-10-13)

**Branch:** `agentdb-12a`.
- 14a was approved and pushed after the secrets gate: `edad515`, `61b6cf7`, `cb04112` and `9d3f757` (`step14a-5-push.out`).
- 14b is one local commit, **not pushed**.

The step made no model calls and no change to EconoContext, the harness or the baselines.
- **Script:** `runs/tier-a/scripts/step14b_rereads.py`.
- **Tables:** `runs/tier-a/step14b/summary.md`, including per-arm blocks.
- **Data:** `runs.csv`, `items.csv` (ids, classes and source hashes only), `edits.csv` and `stale.csv` (TBLite text only).

**Everything here is a fixed-trajectory estimate on the canonical track** (Eq. 9 and the cache simulator on our stable rebuild), not live behaviour.

## The three numbers (canonical track)

| | TBLite (130 runs) | BrowseComp-Plus pilot (12 runs) |
|---|---|---|
| **(1) E: exact references, after repairs** | **0.00%** of total FLOPs | **1.58%** (base 11.57%, CLM 0.99%) |
| **(2) beyond E: V diffs, then P chunk pointers** | **0.49%** (V 0.00%, P 0.49%) | **0.44%** (V 0.00%, P 0.44%) |
| **(3) CLM's plausibly avoidable management cost** (retained text whose cache reuse its edits lost; an upper bound) | **4.96%** of CLM's FLOPs | **23.47%** of CLM's FLOPs, of which rollbacks 20.51% |
| potentially stale current-state evidence (V-class) | 0.00% of calls | 0.00% |
| unsupported (candidate tokens CLM's edits put out of reach) | 6.05% (managed arms) | 10.67% (CLM) |
| N mass; R? mass | 0; 0 | 0; 0 |

**In one line each:**
- **Versions, diffs and chunks add little beyond exact hashing.** No V diff was ever under 30% of the full text. P's exact chunks add about 0.5% of FLOPs on each benchmark. Exact hashing itself saves nothing on TBLite: no tool output of 200+ tokens is repeated byte for byte. On BrowseComp-Plus it saves 11.6% of FLOPs for base, almost all from documents returning inside new search results.
- **The stale-copy problem is not visible here.** No V-class file source ever had only an older version visible. A broader check (any file read whose later write is not visible) finds 8.4% of calls in the managed arms, but in 10 inspected cases the agent never used outdated content (below).
- **CLM spends little generating its management, but its edits cost a lot in lost cache reuse.**
  - **Generation on management:** 1.4% of CLM's FLOPs on TBLite and 3.2% on BrowseComp-Plus.
  - **Lost reuse:** 5.0% on TBLite and 23.5% on BrowseComp-Plus.
  - On BrowseComp-Plus, most of that loss comes from its budget rollbacks, whose notice is inserted near the start of the prompt (question 2 below).

## Definitions as applied (your amendment)

**Units.**
- **A tool output as first shown:** its body is the displayed text without the exit-status line and CLM's `[context: ~N/M tokens]` footer.
- **Size:** items of 200+ tokens are analysed. The mass below 200 is TBLite 87,253 tokens and BrowseComp-Plus 6,952.
- **BrowseComp-Plus documents:** each document inside a search result is also a unit (its 512-token snippet, docid and rank), and each opened document is one unit.

**Classes.**
- **E:** a byte-identical body. A reference restates the new event's source, rank, version and exit status.
  - BrowseComp-Plus has incidental E (12,905 tokens for base, 35,119 for CLM) and **no deliberate E**: no document was opened twice.
- **N:** equal after normalizing ISO datetimes, dates, clock times, durations, PIDs, hex addresses, temp paths and progress counters (`VOLATILE` in the script). It is not savable. **Its mass is 0.**
- **V:** the same source and projection, with evidence of a revision in between (a write, `sed -i`, redirect, `cp`, `mv`, `patch`, git or a build step). For a command source, any revision in the workspace counts.
  - Different ranges or passages are projections, and go to P or U.
  - V is 0.66% of TBLite's tool-output tokens, all from command sources, and 0.38% on BrowseComp-Plus.
- **R?:** the same source and projection, a different body, and no evidence. **0.**
- **P:** exact chunk matches only. Chunks are content-defined: a line boundary chosen by hash, about 120–600 characters, with long lines cut by a CRC rolling hash.

**The stateful replay.**
- Each logged request is rewritten in order; it is not a subtraction from the logged prompts.
- An item becomes a reference only when an identical body is present, as a body, in the same request after that request's edits.
- A reference whose original later disappears is restored, and counted as a **repair**: its cost is priced by the cache simulator. **There were 0 repairs**: references were only made where the original was visible, and in no case did CLM drop an original while keeping its copy.
- Candidates that appear only inside CLM's rewritten text are **unsupported**.

**Costs and the ladder.**
- **References:** three formats (an entry note, an XML-style tag, and a sentence), each restating entry id, source, rank, version and exit. Each was tokenized for real, and the median-cost one was used.
- **Diffs:** real unified diffs (`n=0`), tokenized.
- **Chunk pointers:** `[lines a-b as in entry #j]`.
- **The ladder:** E, then V diffs on top, then P on top. Every step is priced as a cumulative configuration, so nothing is summed twice.

## Per benchmark, per arm (highlights; `summary.md` has every table)

**TBLite.**
- **Arms without edits** (raw, raw-32k, base-32k; 34 runs):
  - E 0%; V 0%;
  - P 9.3% of tool-output tokens, 3.9% in shared spans, 78.6% of them visible;
  - P saves 0.79% of FLOPs, with 4 of 34 runs giving 80% of the saving.
- **Arms with edits** (econo+jev, summary, CLM; 96 runs):
  - P saves 0.36%, with 15 of 96 runs giving 80%;
  - V tokens 0.66%, every one with its earlier version visible, but no diff under 30%.
- **CLM's calls:** management-only 1.8%, mixed 0.2%, task-only 96.7%, no command 1.3%.
- **CLM's 33 edits:**

| edit type | edits | new text | reasoning made visible | new observations | retained, reuse lost | share of CLM's FLOPs |
|---|---|---|---|---|---|---|
| summarize | 19 | 58,596 | 38,282 | 11,782 | 59,472 | 3.14% |
| shorten | 14 | 17,694 | 36,936 | 6,528 | 34,963 | 1.82% |

**BrowseComp-Plus pilot.**
- **Base** (4 runs):
  - E (snippets returning) is 15.4% of tool-output tokens, 93.8% of it with a visible earlier copy;
  - it saves **11.57% of FLOPs** (15.7% of new-prefill FLOPs), with 3 of 4 runs giving 80%.
- **CLM** (8 runs):
  - E is 6.4% of tokens, but only 24.9% has a visible earlier copy: CLM's own edits remove the earlier results;
  - it saves 0.99%.
- **CLM's calls:** management-only 17.4%, mixed 1.6%, task-only 66.7%, no command (empty turns) 14.4%.
- **CLM's 87 edits:**

| edit type | edits | new text | reasoning made visible | new observations | retained, reuse lost | share of CLM's FLOPs |
|---|---|---|---|---|---|---|
| summarize | 51 | 100,237 | 79,270 | 10,653 | 49,588 | 2.38% |
| shorten | 6 | 8,508 | 10,564 | 1,215 | 12,263 | 0.59% |
| rollback (budget control) | 30 | 8,919 | 916 | 615 | 421,325 | **20.51%** |

**Reading the edit tables.**
- **"Reasoning made visible"** is a new category, added because the data showed it. It is text CLM keeps in its context (the model's earlier thinking) that the gateway had stripped from requests, and that a context sync turns into message text. It is new prefill, not avoidable reuse.
- **These are upper bounds:** every token of retained text after the first changed position is counted as lost reuse.

**The 10 inspected TBLite cases** (the broader staleness definition; there are no V-class cases; `step14b-3-stale-cases.out`). None relied on outdated content:
1. **clm-32k scan r3, call 36:** the script was patched at call 35 by the agent itself; the next action runs it. Not reliant.
2. **econo+jev scan r1, call 45:** the "revision" (call 41) writes a copy under /tmp, so the detector is wrong; the next action reruns the script. Not reliant.
3. **clm-full scan r1, call 21:** the "revision" is `awk` with `>=`, read-only, so the detector is wrong; the next action reads the file again. Not reliant.
4. **clm-full scan r1, call 24:** a real `sed -i` at call 23; the next action runs the script. Not reliant.
5. **clm-full scan r1, call 22:** the detector is wrong (`awk`); the next action reads line 79 afresh. Not reliant.
6. **clm-full scan r1, call 30:** a real Python patch at call 26; the next action greps and reads afresh. Not reliant.
7. **clm-32k scan r3, call 27:** a real patch at call 24; the next action re-reads the region. Not reliant.
8. **econo+jev scan r1, call 40:** the "revision" builds /tmp test files, so the detector is wrong. The next action rewrites copies with `sed -e` patterns on lines the patch did not touch. Not reliant.
9. **clm-full scan r1, call 31:** a real patch at call 26; the next action traces a run (`bash -x`). Not reliant.
10. **econo+jev scan r1, call 41:** the "revision" writes /tmp copies, so the detector is wrong; the next action uses `sed -e` on unchanged lines. Not reliant.

**The detector over-flags:** 4 of 10 cases are false positives (`>=` matched as a redirect, or a path named in a command that writes elsewhere). Its 8.4% is an upper bound.

## Answers to your three questions

**1. Reconciling 13b.** The two sets of numbers measure different things. Both are priced on the canonical rebuild, and both use the no-eviction stream as the reference.

| | step-13 report (`61b6cf7`) | 14a |
|---|---|---|
| rule | evict when the next prompt passes **80%** of the limit, **in one batch down to 60%** | **forced**: only when the next call would not fit, the **minimum** needed |
| runs | all **130** | only the **31** whose stream passes the limit |
| knowing's order | oldest first among never-needed items | cheapest first |
| a run that cannot fit | (not applicable: it keeps going over the limit) | **stops**, and does not reach its answer |
| oldest | +14.8% | +10.7% |
| knowing | +8.1% | −8.3% |

**Both are correct for their own rules, but 14a's −8.3% for knowing is not like for like.** 13 of the 31 runs could not fit under knowing and stopped early, and a run that stops costs less.

On the **14 runs where all three eviction policies reach their answer** (`step14b-q1-reconcile.out`), against no eviction:
- oldest: **+49.0%**;
- cheapest: **+9.4%**;
- knowing: **+11.3%**.

**Every eviction rule costs more than not evicting, and knowing does not beat cheapest.** Use these like-for-like figures, not 14a's −8.3%.

**2. CLM's 34,393-token prompt on question 986.**
- **The context file changed:** at its turn 74, CLM's model ran a Python edit of its context file, meant to "collapse all tool outputs". The sync then rebuilt the conversation from that file, which also holds the model's earlier thinking. About 62,000 characters of reasoning, which our gateway had always stripped, became plain message text. CLM's context went from 64,984 reasoning characters and 42,342 content characters to 2,491 and 106,759.
- **Purdue's count jumped:** the next request, gateway call 75, grew from 14,643 to 34,393 tokens on Purdue's count.
- **CLM's count did not:** CLM counts with tiktoken, including reasoning (about 31,000 tokens both before and after), scaled by a ratio learned from Purdue's reported sizes. Because Purdue never saw the reasoning, that ratio had drifted to about 0.57. CLM's check therefore saw about 17,700 tokens, under its 21,512-token limit, and no rollback or final turn fired (CLM's own `timing.json` shows no budget event).
- **In general, yes, CLM's budget control can be bypassed for one call:** whenever the share of text the server does not see changes abruptly (here, reasoning turned into content by its own sync), its calibrated estimate lags. It recalibrates on the next response; call 76 was 4,664 tokens. CLM was not changed.

**3. "Base's stop rule reaches 0 of 31 answers."** The replay's stop rule ends each run at its first call over the limit. Every one of the 31 included runs has such a call before its logged answer, so none reaches it.

The real Base rule then grants one final turn, in which the agent can still submit; the replay did not model that turn. So 0/31 is a property of the replay, not of Base.

## What comes next (your call)
On these logs, a prompt-as-a-table with exact references would pay mainly on search-heavy work: BrowseComp-Plus base saves 11.6%, TBLite 0%. Versions, diffs and chunks add about half a percent, and stale copies were not an issue in the logs we have. CLM's largest avoidable cost on BrowseComp-Plus is the placement of its own rollback notices.
