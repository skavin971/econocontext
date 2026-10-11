# Raw vs EconoContext vs CLM on TBLite: Qwen3.8 on Purdue, real settings (2026-10-11)

**What this is:** the first full comparison of four context arms on the 10 frozen TBLite tasks, 3 repeats each.
- The model is Qwen3.8-27B-FP8 on Purdue GenAI Studio.
- Every call went through our gateway.
- Compute is measured as prefix-reuse FLOPs (CLM, Appendix C).

**The question:** in real settings, how do the arms compare on quality (pass rate, overflows) and on compute? FLOPs are compute, not cost. Dollar claims come only from real bills (tier C).

**Real settings only:** there are no artificial budgets. The 8k CLM run of step 7 was a measurement test and is not part of these results.

**EconoContext ran exactly as it is.** Its rules, prices and thresholds are unchanged (`config/v2.yaml`). This run is the baseline we design against next. No conclusions are drawn here beyond what the numbers show.

## 1. Setup

| | |
|---|---|
| Model | `qwen3.8:27b` = Qwen/Qwen3.8-27B-FP8 (rev 017b9c7) on Purdue GenAI Studio (vLLM 0.30); window 65,536 tokens |
| Gateway | Ours (`gateway/`); every call logged. It sets `reasoning_effort=medium` and `return_token_ids`, removes earlier reasoning from assistant messages, and allows 20 requests a minute |
| Tasks | The 10 frozen TBLite tasks (`benchmarks/tblite/tasks.py`) |
| Repeats | 3 per task per arm (r1–r3), label `tc9`, interleaved (for each repeat, for each task, all 4 arms), 2 workers; the 3 scan-linux r3 trials that never started were rerun one at a time (see the notes) |
| When | 2026-10-11, 03:03–07:37 UTC (4 h 34 min), on AC power, no sleep; the 3 reruns 13:54–14:21 UTC |
| `raw` | Our agent (`agents/react/agent.py`); keeps everything; the model's real window |
| `econo+jev` | Our agent plus EconoContext as it is (Jev, cache in decisions on); the real window |
| `clm-32k` | CLM's agent (`clm-minimal`, submodule at 18dc111) at its published setting, `context_budget_tokens=32000` |
| `clm-full` | CLM's agent at the real window, `context_budget_tokens=57344` (65,536 − 8,192) |
| Same for every arm | `max_tokens` 8,192; 64 steps; `reasoning_effort` medium |
| Temperature (as sent) | Ours 0.7 (no `top_p`); CLM 0.7 with `top_p` 0.95 and `enable_thinking: true` |
| A difference found | The shell command timeout: ours 180 s (the timeout is reported to the model); CLM 120 s (the run stops; one CLM trial was lost to it) |
| Overflow | A request over 65,536 tokens is refused by Purdue, and the run counts as a FAIL |
| Compute | Prefix-reuse FLOPs (CLM Eq. 9) on our rebuild of every prompt, validated token by token against Purdue's own ids. C_token = 48,701,112,320 and C_attn = 393,216 (Eq. 7, equal to CLM's for this architecture) |

## 2. Per task and arm

Passes out of the repeats (an overflow is a FAIL; NG = not graded), mean PFLOPs per trial (min-max), mean calls, ΣU (uncached prompt tokens), ΣG (output tokens, thinking included) and overflows. Means are over the trials that made calls, whatever their outcome.

| task | arm | passes | PFLOPs mean (range) | calls | ΣU | ΣG | overflows |
|---|---|---|---|---|---|---|---|
| acl-permissions-inheritance | raw | 3/3 | 0.143 (0.131-0.150) | 4.3 | 2,005 | 905 | 0 |
| acl-permissions-inheritance | econo+jev | 3/3 | 0.136 (0.114-0.154) | 4.3 | 1,770 | 1,003 | 0 |
| acl-permissions-inheritance | clm-32k | 3/3 | 0.198 (0.169-0.232) | 5.0 | 3,213 | 799 | 0 |
| acl-permissions-inheritance | clm-full | 3/3 | 0.226 (0.194-0.260) | 6.7 | 3,483 | 1,081 | 0 |
| anomaly-detection-ranking | raw | 3/3 | 0.547 (0.504-0.605) | 9.7 | 8,487 | 2,321 | 0 |
| anomaly-detection-ranking | econo+jev | 3/3 | 0.589 (0.466-0.666) | 10.3 | 8,710 | 2,906 | 0 |
| anomaly-detection-ranking | clm-32k | 3/3 | 0.595 (0.512-0.666) | 10.0 | 9,292 | 2,424 | 0 |
| anomaly-detection-ranking | clm-full | 3/3 | 0.655 (0.653-0.656) | 10.0 | 9,879 | 2,989 | 0 |
| api-endpoint-permission-canonicalizer | raw | 2/3 | 1.433 (0.912-2.298) | 13.0 | 7,700 | 20,422 | 0 |
| api-endpoint-permission-canonicalizer | econo+jev | 3/3 | 2.774 (1.235-3.668) | 29.0 | 13,557 | 39,914 | 0 |
| api-endpoint-permission-canonicalizer | clm-32k | 3/3 | 1.187 (0.916-1.553) | 15.7 | 8,961 | 14,285 | 0 |
| api-endpoint-permission-canonicalizer | clm-full | 3/3 | 1.017 (0.871-1.093) | 12.3 | 7,754 | 12,277 | 0 |
| chained-forensic-extraction_20260101_011957 | raw | 3/3 | 0.529 (0.402-0.634) | 11.0 | 7,198 | 3,326 | 0 |
| chained-forensic-extraction_20260101_011957 | econo+jev | 3/3 | 1.297 (0.897-1.817) | 15.7 | 14,114 | 10,924 | 0 |
| chained-forensic-extraction_20260101_011957 | clm-32k | 3/3 | 0.679 (0.463-1.057) | 9.3 | 8,660 | 4,697 | 0 |
| chained-forensic-extraction_20260101_011957 | clm-full | 2/3 | 0.754 (0.595-0.843) | 11.3 | 9,720 | 5,091 | 0 |
| maven-slf4j-conflict | raw | 3/3 | 0.883 (0.748-0.958) | 19.0 | 10,352 | 6,917 | 0 |
| maven-slf4j-conflict | econo+jev | 3/3 | 0.899 (0.743-1.007) | 21.3 | 10,844 | 6,753 | 0 |
| maven-slf4j-conflict | clm-32k | 3/3 | 1.119 (0.937-1.324) | 19.7 | 16,105 | 5,725 | 0 |
| maven-slf4j-conflict | clm-full | 2/3 (1 NG) | 1.100 (0.892-1.282) | 18.3 | 14,955 | 6,460 | 0 |
| pandas-etl | raw | 3/3 | 0.231 (0.206-0.262) | 5.7 | 3,378 | 1,285 | 0 |
| pandas-etl | econo+jev | 3/3 | 0.232 (0.203-0.263) | 6.0 | 3,464 | 1,221 | 0 |
| pandas-etl | clm-32k | 3/3 | 0.407 (0.258-0.566) | 7.0 | 6,752 | 1,415 | 0 |
| pandas-etl | clm-full | 3/3 | 0.410 (0.402-0.420) | 6.0 | 6,920 | 1,328 | 0 |
| sales-data-csv-analysis | raw | 3/3 | 0.598 (0.541-0.696) | 7.0 | 6,684 | 5,196 | 0 |
| sales-data-csv-analysis | econo+jev | 3/3 | 0.623 (0.501-0.720) | 6.7 | 7,350 | 5,007 | 0 |
| sales-data-csv-analysis | clm-32k | 3/3 | 0.615 (0.582-0.653) | 4.7 | 6,816 | 5,363 | 0 |
| sales-data-csv-analysis | clm-full | 2/3 | 0.644 (0.531-0.706) | 5.0 | 7,639 | 5,105 | 0 |
| bandit-delayed-feedback | raw | 0/3 | 6.347 (3.198-9.599) | 39.7 | 43,399 | 63,413 | 1 |
| bandit-delayed-feedback | econo+jev | 0/3 | 6.545 (5.654-7.886) | 43.0 | 44,931 | 67,069 | 1 |
| bandit-delayed-feedback | clm-32k | 2/3 | 3.463 (2.129-4.599) | 28.7 | 31,883 | 32,423 | 0 |
| bandit-delayed-feedback | clm-full | 2/3 | 3.720 (3.077-4.416) | 30.0 | 29,382 | 37,522 | 0 |
| malicious-package-forensics | raw | 3/3 | 4.063 (3.143-5.775) | 13.0 | 40,664 | 28,361 | 0 |
| malicious-package-forensics | econo+jev | 3/3 | 2.971 (2.778-3.300) | 14.7 | 36,287 | 15,902 | 0 |
| malicious-package-forensics | clm-32k | 3/3 | 3.704 (2.135-4.576) | 12.7 | 57,354 | 10,883 | 0 |
| malicious-package-forensics | clm-full | 3/3 | 4.651 (2.616-6.666) | 16.7 | 67,033 | 13,933 | 0 |
| scan-linux-persistence-artifacts | raw | 2/3 | 6.272 (5.433-6.911) | 33.7 | 43,893 | 66,541 | 1 |
| scan-linux-persistence-artifacts | econo+jev | 3/3 | 3.960 (2.311-5.832) | 33.7 | 28,699 | 43,381 | 0 |
| scan-linux-persistence-artifacts | clm-32k | 3/3 | 3.192 (2.110-3.903) | 28.3 | 32,493 | 27,041 | 0 |
| scan-linux-persistence-artifacts | clm-full | 3/3 | 3.232 (2.772-3.855) | 27.0 | 28,551 | 30,664 | 0 |

## 3. Per arm

Total PFLOPs over every trial that made calls, split into linear prefill (C_token·ΣU), linear output (C_token·ΣG) and attention; and PFLOPs on the 22 cells (task, repeat) where all four arms passed (like-for-like work). C_token = 48,701,112,320.

| arm | passes / 30 | graded | overflows | total PFLOPs | linear prefill | linear output | attention | PFLOPs on the 22 all-pass cells | calls |
|---|---|---|---|---|---|---|---|---|---|
| raw | 25 | 30 | 2 | 63.13 | 25.39 | 29.03 | 8.72 | 34.43 | 468 |
| econo+jev | 27 | 30 | 1 | 60.08 | 24.80 | 28.36 | 6.93 | 33.69 | 554 |
| clm-32k | 29 | 30 | 0 | 45.48 | 26.52 | 15.35 | 3.60 | 29.61 | 423 |
| clm-full | 26 | 29 | 0 | 49.23 | 27.08 | 17.01 | 5.14 | 31.84 | 430 |

## 4. The three long-context tasks

These are the tasks where context management can matter: scan-linux, bandit and malicious-package.

| task | arm | passes | overflows | mean peak P | PFLOPs mean | PFLOPs by repeat |
|---|---|---|---|---|---|---|
| scan-linux-persistence-artifacts | raw | 2/3 | 1 | 43,622 | 6.272 | r1 5.433 pass r2 6.911 OVF r3 6.471 pass |
| scan-linux-persistence-artifacts | econo+jev | 3/3 | 0 | 28,419 | 3.960 | r1 5.832 pass r2 2.311 pass r3 3.738 pass |
| scan-linux-persistence-artifacts | clm-32k | 3/3 | 0 | 24,501 | 3.192 | r1 3.903 pass r2 2.110 pass r3 3.564 pass |
| scan-linux-persistence-artifacts | clm-full | 3/3 | 0 | 27,398 | 3.232 | r1 3.855 pass r2 2.772 pass r3 3.068 pass |
| bandit-delayed-feedback | raw | 0/3 | 1 | 43,101 | 6.347 | r1 9.599 OVF r2 6.244 fail r3 3.198 fail |
| bandit-delayed-feedback | econo+jev | 0/3 | 1 | 44,596 | 6.545 | r1 5.654 fail r2 6.096 fail r3 7.886 OVF |
| bandit-delayed-feedback | clm-32k | 2/3 | 0 | 23,841 | 3.463 | r1 4.599 fail r2 2.129 pass r3 3.661 pass |
| bandit-delayed-feedback | clm-full | 2/3 | 0 | 29,123 | 3.720 | r1 3.666 pass r2 4.416 pass r3 3.077 fail |
| malicious-package-forensics | raw | 3/3 | 0 | 40,568 | 4.063 | r1 5.775 pass r2 3.143 pass r3 3.271 pass |
| malicious-package-forensics | econo+jev | 3/3 | 0 | 35,576 | 2.971 | r1 2.835 pass r2 3.300 pass r3 2.778 pass |
| malicious-package-forensics | clm-32k | 3/3 | 0 | 26,648 | 3.704 | r1 4.401 pass r2 4.576 pass r3 2.135 pass |
| malicious-package-forensics | clm-full | 3/3 | 0 | 42,078 | 4.651 | r1 6.666 pass r2 4.672 pass r3 2.616 pass |

## 5. What EconoContext and CLM did

| task | EconoContext: arrival full/slice/preview | evictions | compactions | Jev asked | Jev fallbacks | CLM-32k edits / rollbacks | CLM-full edits / rollbacks |
|---|---|---|---|---|---|---|---|
| acl-permissions-inheritance | 0/0/0 | 0 | 0 | 0 | 0 | 0 / 0 | 0 / 0 |
| anomaly-detection-ranking | 0/1/0 | 0 | 0 | 1 | 0 | 0 / 0 | 0 / 0 |
| api-endpoint-permission-canonicalizer | 0/3/0 | 0 | 0 | 3 | 0 | 0 / 0 | 0 / 0 |
| chained-forensic-extraction_20260101_011957 | 0/1/0 | 0 | 0 | 3 | 0 | 0 / 0 | 0 / 0 |
| maven-slf4j-conflict | 0/3/0 | 0 | 0 | 3 | 0 | 0 / 0 | 0 / 0 |
| pandas-etl | 0/0/0 | 0 | 0 | 0 | 0 | 0 / 0 | 0 / 0 |
| sales-data-csv-analysis | 0/0/0 | 0 | 0 | 0 | 0 | 0 / 0 | 0 / 0 |
| bandit-delayed-feedback | 0/1/0 | 0 | 0 | 13 | 0 | 3 / 0 | 0 / 0 |
| malicious-package-forensics | 6/15/0 | 0 | 1 | 31 | 1 (Jev HTTP 400 max_tokens_exceeded x1) | 6 / 0 | 1 / 0 |
| scan-linux-persistence-artifacts | 4/7/0 | 0 | 0 | 28 | 0 | 3 / 0 | 1 / 0 |

- **EconoContext, over its 30 trials:**
  - 10 arrivals kept in full and 31 sliced, 0 previews;
  - 0 evictions and 1 compaction (malicious-package);
  - Jev asked 82 questions, with 1 fallback (max_tokens_exceeded).
- **CLM:** clm-32k edited its context 12 times and clm-full 2 times. Neither ever hit its budget, so there were 0 forced rollbacks.

## 6. Paired comparisons with raw, by task and repeat

PFLOPs: the arm's trial minus raw's in the same task and repeat (negative = the arm used less), over the pairs where both trials passed (like-for-like), and over all pairs where both made calls. Outcomes: pairs where both were graded; the sign test counts the pairs where exactly one of the two passed. Exact two-sided sign tests; with n = 3 per task these are small samples.

| arm vs raw | pairs (both passed) | mean ΔPFLOPs | median Δ | arm lower / higher | sign p | pairs (both ran) | mean ΔPFLOPs | arm lower / higher | sign p | outcome pairs | arm-only pass / raw-only pass | sign p |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| econo+jev | 25 | +0.025 | +0.023 | 10 / 15 | 0.424 | 30 | -0.102 | 13 / 17 | 0.585 | 30 | 2 / 0 | 0.500 |
| clm-32k | 25 | -0.168 | +0.061 | 8 / 17 | 0.108 | 30 | -0.589 | 11 / 19 | 0.200 | 30 | 4 / 0 | 0.125 |
| clm-full | 22 | -0.118 | +0.117 | 6 / 16 | 0.052 | 30 | -0.464 | 11 / 19 | 0.200 | 29 | 4 / 2 | 0.688 |

n = 3 per task (at most 30 pairs per comparison, fewer once ungraded trials are removed). These are small samples. The table states what was measured; it supports no claim of a general effect.

## 7. Checks

- **Validation:** 1,875 of 1,875 answered calls validated (0 invalid), over 120 measured trials; 0 trial(s) made no call.
- **Totals re-derived from calls.jsonl** (the server's own usage) against measure/ (our rebuild):

| arm | answered calls (log / measured) | ΣG server | ΣG measured | ΣP server | ΣP rebuilt | ΣP difference |
|---|---|---|---|---|---|---|
| raw | 468 / 468 | 596,064 | 596,064 | 7,463,899 | 7,464,124 | -225 |
| econo+jev | 554 / 554 | 582,242 | 582,242 | 7,803,452 | 7,803,739 | -287 |
| clm-32k | 423 / 423 | 315,164 | 315,164 | 4,050,126 | 4,050,128 | -2 |
| clm-full | 430 / 430 | 349,346 | 349,346 | 5,191,157 | 5,191,164 | -7 |

ΣG must match exactly. ΣP differs only by Purdue's random reordering of tool-schema keys, which changes the served prompt by a few tokens per call (validation accepts it only inside the tools block).

- **CLM cross-check** (`tests/measure/clm_crosscheck.py --from-calls`, CLM's own block functions on our rebuilt token ids, all 60 CLM runs):
  - 60 runs: prefill exactly equal in 60; equal except fully covered prompts (identical repeats, or exact earlier prefixes on a block boundary) in 0, where our extra uncached tokens equal the predicted gap exactly ([]); unexplained differences: 0
  - decode attention pairs, CLM's averaged formula vs per-call G_t: median -7.6%, range -37.3% to +22.0%
  - the same gap as a share of total F: median -0.133%, range -1.822% to +1.002%

  Prefill is exactly equal in every run. The decode term differs by design: CLM averages generation over turns, while Eq. 9 uses each call's own output length.

## Trials that need a note

- **Not graded (1).** clm-full on maven r3 stopped on CLM's 120 s command timeout, after 20 validated calls.
- **Reruns (3).** In the overnight run, three arms' Docker environments on scan-linux r3 did not start:
  - econo+jev and clm-full: `EnvironmentStartTimeoutError` after 600 s;
  - clm-32k: `docker compose` failed (BuildKit: "lease does not exist").

  The cause was two workers building the same image at once. Harbor serializes builds only within one process. Those trials made 0 calls.

  In step 10a they were rerun with tc9's settings (Jev at 2.5 characters per token, CLM's 120 s command timeout, max_tokens 8,192). The image was built once first, then the three trials ran one at a time. All three passed: econo+jev in 30 calls, clm-32k in 42, clm-full in 21.

  The failed attempts are kept in `runs/tc9-envfail/`.
- **Overflows (3, counted as FAIL):** raw bandit r1, raw scan-linux r2 and econo+jev bandit r3. Neither CLM arm overflowed.
- **Calls:** 1,878 logged, of which 1,875 were answered and measured; the 3 refused were the overflows. There were 0 upstream retries.

## Files

- **Per run, call, task and arm:** `runs/tc9/report.csv` (runs), `runs/tc9/calls.csv` (calls), `runs/tc9/arms.csv` (task × arm), `runs/tc9/profile.csv` (context profile).
- **The run:** `runs/tier-a/step9-progress.md` (every trial and gate decision); `runs/tier-a/step9a-report.md` and `runs/tier-a/step9b-report.md`.
- **The reruns:** `runs/tier-a/step10a-progress.md` and `runs/tier-a/step10a-rerun-*.out`; the failed first attempts in `runs/tc9-envfail/`.
- **Raw evidence:** `runs/tc9/<arm>/<task>-r<n>/` (summary.json and the Harbor trial); `runs/gateway/tc9.*/calls.jsonl` (every call as sent and as returned).
- **Rebuilt by:** `.venv/bin/python -m measure.report runs/tc9`, `-m measure.profile runs/tc9`, `-m measure.compare runs/tc9`.
