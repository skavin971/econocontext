# Step 12f report: information-gathering sequences (offline audit, 2026-10-12)

**Branch:** `agentdb-12a`.
- 12f is one local commit, **not pushed**.
- `401773a` (12d) is also still unpushed; you did not ask for a push this time.

The audit made no API calls and changed no behaviour. It covers the same 130 runs as 12b–12d (tc9, tc10s, tc11v).
- **Script:** `runs/tier-a/scripts/step12f_sequences.py`.
- **Tables:** `runs/tier-a/step12f/summary.md`. The broader-rule check is in `runs/tier-a/step12f/owner_rule/`.
- **Per-turn and per-sequence data:** `turns.csv` and `sequences.csv`.

**Scope: this measures TBLite's opportunity only, not the value of the idea in general.** TBLite tasks are mostly building and fixing: our agent and CLM gather information mainly through scripts (`python3 - <<EOF …`) and compound commands, not through runs of plain reads. A benchmark built around search and reading would give very different numbers. The last section estimates one, BrowseComp-Plus, from the CLM paper; it is an estimate, not a measurement.

## Definitions
- **Turns:** the agent calls of a run. Summary calls are not turns, and they do not break a sequence.
- **Information-gathering turn:** every tool call in the reply is read-only, using your list:
  - `read_file`;
  - `bash` whose every part (split at `&&`, `||`, `;`, `|` and newlines) is `cat`, `grep`, `find` (without `-exec` or `-delete`), `ls`, `sed -n`, `head` or `tail`. I also allowed `cd`.
  - No output redirection (except `2>/dev/null` and `2>&1`), no heredoc, no `$( )`.
- **Sequence:** a maximal run of such turns. It ends at the first other turn: a write, a test run, submit, any other command, or a reply without a tool call.
- **A sequence's FLOPs,** from Eq. 9 terms attributed to the tokens that cause them:
  - **generated:** thinking and visible tokens, each counted with its decode attention;
  - **admitted:** its tool outputs' tokens, wherever they are computed in later prompts, with their prefill attention;
  - **re-read:** its replies' tokens, wherever they are computed in later prompts, with their prefill attention.

  The attention later tokens pay to these tokens while they sit in context is reported apart, as "resident", and is not in the shares.
- **Labels** (turns after the first in a sequence, against the previous turn's tool output). Targets are read_file's path, or each path or symbol argument in the command:
  - **(b) choice among visible candidates:** a path target matches one the previous output lists, and it lists 2 or more. "Listed" means a path or a file name that opens a line (as `find` and `grep -n` print it) or closes one (as `ls -l` prints it).
  - **(a) exact follow-up:** otherwise, a path (or its file name) or a symbol appears verbatim in the previous output.
  - **(c) new:** anything else.
- **Short and long tasks** were not defined in the project, so I used one rule, which you can change: rank the 10 tasks by the raw arm's mean agent calls in step 9 (3 repeats) and split at the median.
  - **Short:** acl (4.3 calls), pandas-etl (5.7), sales-data (7.0), anomaly-detection (9.7), chained-forensic (11.0).
  - **Long:** api-endpoint (13.0), malicious-package (13.0), maven (19.0), scan-linux (33.7), bandit (39.7).

## Results (shares of the arm's total FLOPs; step 9, 30 runs per arm)
Under your rule, **148 of 2,190 agent turns** are information gathering. They form only **19 sequences of 2+ turns** (17 of 2 turns, 2 of 3). Of the 21 turns that get a label, only 4 are (a) or (b).

| step 9 arm | read-only turns | sequences of 2+ | FLOPs in 2+ sequences | in (a)+(b) turns | labels a/b/c |
|---|---|---|---|---|---|
| raw | 27 of 468 | 2 | 2.6% | 0.1% | 1/0/1 |
| econo+jev | 41 of 553 | 5 | 3.4% | 0.1% | 1/0/4 |
| clm-32k | 36 of 423 | 5 | 2.8% | 0.0% | 0/0/6 |
| clm-full | 38 of 430 | 6 | 3.0% | 0.1% | 1/1/5 |

- **Short vs long tasks.**
  - On short tasks, only raw has a sequence of 2+ turns: 1.7% of FLOPs, all of it an (a) turn (1.5%).
  - On long tasks, sequences of 2+ turns hold 2.7–4.0% of FLOPs, and (a)+(b) turns 0.0–0.1%.
- **10d and step 11:** sequences of 2+ turns hold 0–0.9% of FLOPs, and no turn is labelled (a) or (b).
- **What a sequence costs** (all 19 sequences): admitted tool outputs are 88% of it, thinking 7.5%, visible text 2.1%, and re-reading its replies 2.0%. The resident attention would add another 0.27× on top.
- **Why so few turns qualify** (`step12f-2-readonly-check.out`). The other turns start with heredocs, redirections or substitutions (991 turns, mostly inline Python), or with `echo` (182), `python3` (144), `timeout` (61), `mvn` (48), `wc` (42) and other commands. There are also 81 replies without a tool call, 69 `write_file` turns, 63 `submit` turns and 27 empty cut replies.

**Sensitivity:** the same audit under EconoContext's own broader read-only rule (`read_only_bash`, which also allows `wc`, `echo`, `which`, `id`, `stat`, `jq` and read-only git) finds 233 read-only turns (`step12f-3-owner-rule.out`).

| step 9 arm | FLOPs in 2+ sequences | in (a)+(b) turns | short tasks: in 2+ / in (a)+(b) | long tasks: in 2+ / in (a)+(b) |
|---|---|---|---|---|
| raw | 6.5% | 3.0% | 5.6% / 3.0% | 6.6% / 3.0% |
| econo+jev | 6.3% | 1.5% | 4.7% / 1.6% | 6.6% / 1.5% |
| clm-32k | 6.7% | 0.8% | 2.9% / 1.7% | 7.5% / 0.6% |
| clm-full | 6.6% | 0.9% | 2.1% / 1.4% | 7.4% / 0.8% |

Under this rule, every match is (b): a file picked from a listing.

**One pattern the labels do not capture** (`step12f-4-label-sample.out`): reading the next range of a file the previous turn just showed (`sed -n '40,80p' auditd.log` after `cat auditd.log`). Its path is not in the previous output's text, so it is labelled (c). There are 12 such turns, 0.3–1.5% of an arm's FLOPs (15 turns and up to 3.3% under the broader rule).

**So on TBLite, an engine that removes (a) and (b) turns without Qwen has about 0.1% of FLOPs to work with under your rule, and 1–3% under the broader one.**

## Estimate: a search-heavy benchmark (BrowseComp-Plus) — AN ESTIMATE, not measured
**What the CLM paper (arXiv 2609.37725) and its repository give for BrowseComp-Plus:**
- **Setup (App. E):** 830 questions over a fixed corpus. The tools are search and get_document. "The search tool returns the top 10 snippets of 512 tokens and the document reader is capped at 8,192 tokens." The budget is 23,560 tokens, with 100 turns and up to 4,096 generated tokens per call (Qwen3.6-27B, thinking on).
- **Compute (CLM repository, `suffix_cache_reuse/assets/scr_bcp830_strip.png`):** CLM with standard SGLang costs 10.98 PFLOPs per question, about 10.1 of it prefill. 72.9% of prompt tokens are prefix-cache hits and 27.1% are prefilled.
- **Not given:** the paper states no calls per question and no typical context size. Both are derived below.

**Derived (`step12f-5-bcp-estimate.out`).** The prefill of 10.1 PF, at a 72.9% hit share, implies a number of calls per question that depends on the mean prompt.
- Constants: our Qwen3.8-27B values, **assumed** to match Qwen3.6-27B.
- **Mean prompt 23,560 tokens** (full budget): about **28 calls per question**.
- **Mean prompt 16,000:** about **43**.
- **Mean prompt 12,000:** about **59**.

For comparison, TBLite clm-32k (tc9) averages 14.1 calls per task with a mean prompt of 9,575 tokens. That is 1.52 PFLOPs per task at an 86.6% hit share; BrowseComp-Plus costs about 7× more per question.

**How the 12f quantities would likely differ:**

| quantity | TBLite (measured) | BrowseComp-Plus (estimate) | basis |
|---|---|---|---|
| tool output size | median 167 tokens; 238 of 2,080 outputs ≥ 1,000 | every output ≥ 1,000: a search is 5,120; a document up to 8,192 | App. E |
| information-gathering turns | 148 of 2,190 (7%) | nearly all turns: the only tools are search and read | App. E |
| FLOPs in sequences of 2+ turns | 2.6–3.4% (6–7% under the broader rule) | most of the run, likely over 80% | every turn but the answer is a read, and prefill is about 92% of FLOPs |
| big outputs' share of FLOPs | 14.9% (12d) | likely 60–85% | ASSUMES tool outputs are 65–90% of each prompt (system prompt, task, search skills and replies the rest) |
| turns labelled (b) (a document picked from a search's 10 results) | 0–0.1% of FLOPs | roughly 25–70% of FLOPs | ASSUMES 30–60% of turns are document reads following a search, and documents of 4,000–8,192 tokens; admitted outputs dominate a turn's FLOPs (88% on TBLite) |
| context pressure | mean prompt 9.6k under a 29,952 limit | a 23,560 budget holds only 3–4 outputs, so CLM edits often; cache hits fall to 72.9% from 86.6% | App. E; repository figure |

**Caveats on this estimate:**
- The ranges rest on the assumptions marked above, not on BrowseComp-Plus trajectories, which we do not have.
- **In BrowseComp-Plus, (b) is harder to remove than on TBLite.** Choosing which of 10 retrieved documents to open is the model's main decision there. An engine could remove those turns only if it could make that choice as well, for example by opening the top-ranked result. Our label (b) does not test that.
- A measured answer needs BrowseComp-Plus trajectories, from CLM's logs or our own runs.
