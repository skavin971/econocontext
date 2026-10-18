# Step 17 report: context policies in FLOPs and in dollars (offline, 2026-10-13)

**Branch:** `agentdb-12a`.
- Step 17 is one local commit, **not pushed**.
- Step 16 (`5e4f968`) is also still unpushed.

The step made no model calls and no code changes outside the analysis scripts.
- **Scripts:** `runs/tier-a/scripts/step17_policies.py` and `step17_summary.py`.
- **Tables:** `runs/tier-a/step17/summary.md`.
- **Data:** `runs.csv` and `cut.csv`: numbers and ids only. The released BrowseComp-Plus runs were read in `~/econo/bcp`.

**Everything here is a fixed-trajectory estimate on the canonical track.** Every policy replays the same replies; only what each prompt holds changes.

**Traces:**
- TBLite, all arms (130 runs);
- the BrowseComp-Plus CLM pilot (8);
- the released GPT-5 BM25 runs (830 questions), tokenized with Qwen and priced with Qwen's constants;
- our BrowseComp-Plus base pilot (4) as a contrast.

**Dollars** are counted in input-token units: uncached input 1, cache read 0.1, output 6.

## The answers

**(1) Rolling observation masking (P2, M = 10) costs more than keeping everything (P0) on every trace set, and the penalty is much larger in FLOPs than in dollars.**

| | P2 FLOPs vs P0 | P2 dollars vs P0 |
|---|---|---|
| TBLite | **263%** | **133%** |
| CLM pilot | 211% | 100% (break-even) |
| GPT-5 runs | 364% | 194% |

- **Why:** each call masks one observation 10 turns back, so the prefix cache breaks there and the last 10 turns are prefilled again. New-prefill tokens rise 4.5× on TBLite (2.1M → 9.4M), while tokens sent fall only 20%.
- **Why dollars forgive more:** P0's long prompts are mostly cache reads, which cost nothing in Eq. 9 but 10% in dollars, so P0 still pays for them.
- **On BrowseComp-Plus the budget dominates:** it binds almost every call (GPT-5: 14,033 budget compactions in 20,326 calls). P2 and P3 are then mostly the oldest-first fallback. TBLite is the clean test of the M = 10 rule (63 compactions in 2,190 calls).

**(2) The best policy is the same under both metrics. The ranking that flips is keep-everything against the budgeted policies.**

| traces | best by FLOPs | best by dollars | P0 (no budget) |
|---|---|---|---|
| TBLite | P4 a=1 oracle 103.0% | P4 a=1 oracle 96.3% | **cheapest in FLOPs** (100%); beaten in dollars by P4 a=1 (both variants) and P5 |
| CLM pilot | P5 107.3% | P5 78.3% | **cheapest in FLOPs**; costs about 28% more in dollars than P5 (78.3%) or CLM as executed (P1, 78.2%) |
| GPT-5 runs | P4 a=1 oracle 78.3% | P4 a=1 oracle 52.4% | beaten in both metrics (it sends 849M tokens) |

- **Among the budgeted policies, the best never changes between metrics.** Only middle places swap: TBLite P5 and P4 a=1 simple; GPT-5 P2 and P3, which are nearly tied.
- **The real flip is P0 against the rest:**
  - In FLOPs, keeping everything is cheapest on TBLite and the CLM pilot, because a growing prompt is almost entirely cache hits.
  - In dollars, every cache hit still costs 10%, so budgeted policies win.
- **CLM as executed (P1) is the same flip:** 115% of P0 in FLOPs, 78% in dollars.

**(3) How far P4 and P5 are from the best** (points of P0):

| | TBLite FLOPs / dollars | CLM pilot FLOPs / dollars | GPT-5 FLOPs / dollars |
|---|---|---|---|
| P4 a=1, simple signal | +9.3 / +2.2 | +21.5 / +4.2 | +4.1 / +2.2 |
| P4 a=1 oracle | **best** | +11.8 / +2.5 | **best** |
| P4 a=2, simple signal | +37.7 / +8.8 | +50.1 / +10.3 | +70.4 / +36.2 |
| P5 two-zone | +5.3 / +3.5 | **best** | +7.6 / +11.2 |

- **Deciding at age 1 is right; age 2 is far worse.** Stubbing a 2-turn-old observation breaks the cache one turn further back, on every call.
- **The simple signal ("referenced by the next reply?") costs 4–10 points of FLOPs against the oracle at age 1** (TBLite 9.3, CLM pilot 9.7, GPT-5 4.1), mostly in restores. On GPT-5 it makes 623 restores against the oracle's 1.
- **P5 is within 5–8 points of the best in FLOPs, and best on CLM's own traces.** In dollars it trails on GPT-5 (+11.2), because it keeps more tokens visible (277M sent against 128M).

## The 68 cut replies (CLM, BrowseComp-Plus)
- **Discarded thinking:** 278,528 tokens (68 × 4,096). Generating them is **14.0%** of CLM's executed FLOPs. Step 16's 21.6% also counted those calls' prefill.
- **The next reply does not restart the same way.**
  - No pair shares even its first token sequence: the median common prefix is 0 tokens, and none has identical first 200 tokens.
  - Only 1 of 68 has a difflib ratio of 0.8 or more on the first 200 thinking tokens.
  - The median ratio is 0.47, against 0.36 for a random reply from the same run (seeded baseline). 15 of 68 beat that baseline by 0.2 or more.
  - 13 of the next replies are cut again.
- **Upper bound for "resume, don't retry":**
  - **0.2%** of CLM's FLOPs (1 reply) at ratio ≥ 0.8;
  - **6.1%** (30 replies) at ratio ≥ 0.5, but the random baseline already reaches 0.5 in 18 cases, so most of that is chance.
  - The cut thinking is lost, but it is not repeated. Resuming would save at most a few percent.

## Method (what I had to decide)
- **Traces:**
  - From our gateway logs: the header (system with tools, and the task), then each agent reply (content and tool calls, reasoning removed as the gateway removes it) and its tool outputs as first shown.
  - Management messages, CLM's re-serialized turns and notices, harness nudges and summary calls are not in the trace, so every policy starts from the bare conversation.
  - Outputs that were never shown in full are not in the traces (rolled back before shown, or first seen re-serialized): TBLite 26, CLM 39.
  - **Rebuild check:** on our unmanaged arms (38 traces), P0 equals what ran to within 0.8% of tokens sent and 0.4% of FLOPs.
- **The released GPT-5 runs:**
  - G is the Qwen count of the reasoning summaries, arguments and answer the run file shows. That underestimates GPT-5's hidden reasoning, so generation costs are low for all policies equally.
  - The header is our BrowseComp-Plus base header with the question.
  - P1 equals P0, since these runs were not managed.
- **Budgets:** TBLite 29,952; BrowseComp-Plus 21,512, for the GPT-5 runs too.
  - When a policy's rule leaves a prompt over the limit, the oldest full observations are stubbed until it fits; this fallback is counted.
  - Observations from the previous turn, or needed by the current reply, are never stubbed by the budget.
  - On TBLite, 21 calls cannot fit under any policy, because the raw arm's own long turns overflow.
- **Needed again** (13b's rule with step 16's names): a later reply quotes 30+ characters of the observation, or names a path, code-like identifier, docid or multi-word proper name from it, **that no other message in that call's context contains**. A needed stub is restored at the tail of that call's prompt and charged there.
  - A first version without the uniqueness condition restored nearly every observation (78 restores on one TBLite run), because paths recur everywhere.
- **Policy choices I made:**
  - P3 applies the M = 10 rule only on calls divisible by 5.
  - P4's oracle stubs at age a when no later reply needs it.
  - P5 stubs instead of deleting, so restores work the same as elsewhere. It compacts only when the budget forces it, cheapest items first (least text after them), down to 60% of the limit: one compaction, then quiet.
- **Cut replies:** finish reason `length`, no command, no content, and not the run's last call.

## Caveats
- **These are fixed trajectories.** A masked or stubbed observation would change what the model writes next. Restores model only the references we can detect.
- **The CLM pilot is 8 runs.**
- **The GPT-5 runs' G is a floor,** so any policy's generation share there is too low.
- **The dollar weights are one illustrative price card** (cache read 10% of input, output 6×).
- **P0 has no budget,** so on TBLite and the CLM pilot it sends prompts no budgeted policy could.

## The decision needed
Approval of steps 16 and 17, and the push of both.

**The result in one line:**
- **FLOPs:** keeping everything is hard to beat under prefix caching. The best budgeted policy is P4 at age 1 (its simple signal within 4–10 points of the oracle) or, on CLM's own traces, P5.
- **Dollars:** those same policies win clearly.
- **Masking every turn** (P2, P3) is the costliest option under both metrics.
