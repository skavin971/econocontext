# Step 12b report: where the FLOPs go, and three Owner fixes (2026-10-12)

**Branch:** `agentdb-12a`.
- `6c1eb7a` (12a) was pushed after the secrets gate (`step12a-9-push.out`).
- 12b is one local commit, **not pushed**.

The analysis made no API calls and changed no behaviour. It covers 130 runs: tc9 (step 9; 4 arms × 30), tc10s (10d; 3 arms × 2) and tc11v (step 11; base-32k × 2, clm-32k, summary-32k).
- **Calls:** 2,194, all validated token for token.
- **FLOPs:** each run's PFLOPs equals its `report.csv` exactly.
- **Files:** `runs/tier-a/step12b/` holds `summary.md` (every table), `runs.csv`, `calls.csv`, `outputs.csv` and `loops.csv`. The scripts are `runs/tier-a/scripts/step12b_analysis.py` and `step12b_summary.py`.

Only the step-9 arms have 30 runs each. The 10d and step-11 rows have 1 or 2 runs each and are only indicative.

## 1. FLOPs split, and new prefill tokens by source
FLOPs are split into prefill of new tokens (C_token·U), generated tokens (C_token·G) and attention:

| step 9 arm | prefill of new tokens | generated | attention |
|---|---|---|---|
| raw | 40% | 46% | 14% |
| econo+jev | 41% | 47% | 12% |
| clm-32k | 58% | 34% | 8% |
| clm-full | 55% | 35% | 10% |

- **Our agent spends about as much on generating as on prefilling;** CLM spends more on prefill.
- **Attention is 8–14%.** Prefill attention and decode attention are about equal.

New prefill tokens U = P − R, split by source, as a share of U:

| step 9 arm | tool outputs | own turns | task + system | re-prefill after edits | other | block tail |
|---|---|---|---|---|---|---|
| raw | 51% | 38% | 10% | 0% | 0.5% | 0.6% |
| econo+jev | 48% | 40% | 10% | 0% | 0.8% | 0.7% |
| clm-32k | 36% | 33% | 15% | 3.7% | 11.5% | 0.6% |
| clm-full | 42% | 29% | 15% | 0% | 13.6% | 0.5% |

- **"Other"** is the harness's own user messages (nudges, notices, summaries) and the template's generation prompt. In CLM's arms it is 11–14%: its notices.
- **Summary arms: re-prefill is 34% (10d) and 42% (step 11) of U,** and 97–100% of that is inside the compaction calls themselves. Codex's compaction request is sent without tools, so the prefix differs from the first token and the whole conversation is prefilled again.
- **Block tail** means tokens recomputed only because the cache works in 16-token blocks.

How the sources are found:
- Each uncached token is assigned to its message by rendering the conversation's prefixes with Qwen's template.
- A prefix may end inside a group of tool results; there, only the group's closing tag differs from the full prompt.
- A message the run had sent before counts as re-prefill. Messages are recognised by their tool call id, or by their text.

## 2. Overlap: lines already visible when a tool output arrives
Lines count as visible if they appear anywhere in the context at that moment; only lines of 8 or more non-space characters are counted.

| step 9 arm | lines already visible | tokens saved by showing only new lines | share of output tokens |
|---|---|---|---|
| raw | 20% | 27,993 | 10.7% |
| econo+jev | 23% | 28,072 | 11.6% |
| clm-32k | 17% | 13,847 | 7.5% |
| clm-full | 17% | 18,919 | 8.5% |

- In the step-11 rows the saving is 8–11%.
- **The exit-code footer is 9–15% of that saving in step 9** (`step12b-4-footer.out`); the rest is repeated content, such as `getfacl` listings and re-read files.
- **The tokens saved are counted once, in the output itself.** They also stay in the context for every later call, which this count does not include.

## 3. Later use of big outputs (1,000+ tokens as shown)
A big output counts as used if a later model turn does any of the following:
- quotes 30 or more characters of it that were not already in the context;
- names a path the producing call read;
- names a path that first appeared in the output.

| step 9 arm | big outputs | never used again | tokens never used again | quoted |
|---|---|---|---|---|
| raw | 58 | 10 (17%) | 21,164 (13%) | 36% |
| econo+jev | 62 | 4 (6%) | 8,757 (7%) | 45% |
| clm-32k | 41 | 4 (10%) | 8,275 (8%) | 39% |
| clm-full | 52 | 7 (13%) | 16,689 (12%) | 38% |

- **The path criteria carry most of the "used" verdicts:** a source path is named in 62–79% of cases.
- **By quotation alone, 55–64% of big outputs are never quoted.**
- In the 10d and step-11 rows (3–7 big outputs per arm), 0–1 big outputs are never used again.

## 4. Loops
Nine loops were found.

- **Two are real error loops.** Both are on bandit, step 9: econo+jev reran a failing `validate4.py` twice, and raw ran a failing patch three times.
- **All seven command loops are replies cut at the 2,048-token output cap.** The cut calls arrive with their arguments dropped (`bash {}`; Base's `write_file` without `content`), so each is the same empty call again. They cost:
  - raw-32k (10d): 4 extra calls, 8.5% of the arm's PFLOPs;
  - clm-32k (step 11): 5 extra calls, 13.2%;
  - summary-32k (10d): 1.3%;
  - base-32k (step 11): 2.2%.

  Step 9 (at 8,192 max tokens) has no command loops. This is the cut-reply issue recorded at the step-11 freeze, measured in calls and FLOPs.

## 5. Thinking against prompt size
- **Thinking is 38–62% of generated tokens.** The mean per agent call is 407–1,060 tokens.
- **Within runs, thinking barely tracks P.**
  - Step 9 median Pearson r: raw +0.03, econo+jev +0.06, clm-full +0.04, clm-32k +0.20.
  - Spearman is higher (+0.20 to +0.38), so the relation is weak, monotone and noisy.
  - About a quarter of runs have r > 0.3, and up to a quarter (raw: 7 of 28) have r < −0.3.
- **Step 11 base-32k is negative in both runs** (median −0.26), but that is 2 runs.

## 6. The three Owner fixes (exact counts throughout)
1. **The note line is included in a preview's or slice's size**, both in the window and in the arrival price. Each form's size is now that of the full text the model would see.
2. **A capped output is sized after the cap:** the window records the text sent, at its size.
3. **The eviction price measures the prompt after the item:** the sum of the window's exact token counts after the item's position. It replaces `json.dumps` of each message.

`window_tokens`, which 12a added to carry the old sizes, is removed from the agent DB copy, and `CHANGES.md` is updated. A unit test checks that a preview is sized with its note line.

**The goldens are updated** to the fixed Owner with exact counts. `test_equivalence.py` is now a regression test against them, and its docstring records the history.

**What changed, per scenario** (`step12b-2-owner-changes.out`). A is the 12a goldens, B is exact counts without the fixes, and C is with the fixes.

- **The fixes (B → C) change no decision and no message to the model in any scenario.** Only prices move:
  - Fix 1 raises each preview or slice price by about 350 in these units: the note line is about 47 tokens. For example, `evict_and_serve` preview 17,430 → 17,783.
  - Fix 2 (capped_full): the capped output's eviction price "keep" goes 54,662 → 38,596, because it is now sized at the 30,000 characters actually sent.
  - Fix 3: the eviction prices move slightly in `predictor_rules` (keep 8,097 → 8,402) and in `capped_full`.
- **The counts alone (A → B) are as reported in 12a.** One scenario changes its decisions: `repeats_and_reads`, where a 100-line file crosses the 1,000-token arrival threshold. Elsewhere only numbers change, and so does the "(about N tokens)" in the arrival note.

**Tests:** 144 pass (`step12b-5-tests.out`), one more than 12a: the note-size test.

## Observations (not acted on)
- **New prefill (40–58%) and generation (34–47%) dominate the FLOPs;** attention is small.
  - **Generation:** our agent's share is the larger, about 46% against CLM's 34%.
  - **Prefill:** tool outputs are the biggest source of new prefill (36–51%), then the model's own turns (29–40%).
- **Overlap: about 10% of tool-output tokens repeat lines already visible.** That is the upper bound of what showing only new lines could save, before counting how long those tokens stay in context.
- **Big outputs never used again: 6–17% of them,** under a generous definition (naming the path counts as use). The quote-only criterion would make it 55–64%.
- **Loops cost little,** except the cut-reply loops at 2,048 max tokens (up to 13% of an arm's FLOPs).
- **Thinking is not driven by prompt size** in these runs.
