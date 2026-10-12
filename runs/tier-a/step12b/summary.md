# Step 12b tables

130 runs, 2194 calls (all validated: 2194), 2080 tool outputs. Each run's PFLOPs match its report.csv (largest relative difference 0.0e+00).


## 1a. FLOPs split (share of the arm's total PFLOPs)

| step | arm | runs | PFLOPs | prefill of new tokens | generated | attention | (attn: prefill) | (attn: decode) |
|---|---|---|---|---|---|---|---|---|
| step 9 | clm-32k | 30 | 45.48 | 58.3% | 33.8% | 7.9% | 4.6% | 3.4% |
| step 9 | clm-full | 30 | 49.23 | 55.0% | 34.6% | 10.4% | 5.9% | 4.5% |
| step 9 | econo+jev | 30 | 60.08 | 41.3% | 47.2% | 11.5% | 4.8% | 6.7% |
| step 9 | raw | 30 | 63.13 | 40.2% | 46.0% | 13.8% | 5.7% | 8.1% |
| 10d | clm-32k | 2 | 2.70 | 65.1% | 28.0% | 6.9% | 4.7% | 2.2% |
| 10d | raw-32k | 2 | 5.55 | 28.3% | 59.7% | 12.0% | 3.3% | 8.7% |
| 10d | summary-32k | 2 | 8.90 | 37.7% | 51.9% | 10.4% | 3.6% | 6.9% |
| step 11 | base-32k | 2 | 5.11 | 37.7% | 54.7% | 7.7% | 3.0% | 4.7% |
| step 11 | clm-32k | 1 | 4.18 | 27.8% | 63.1% | 9.1% | 2.6% | 6.5% |
| step 11 | summary-32k | 1 | 8.41 | 61.7% | 28.7% | 9.7% | 6.1% | 3.6% |

## 1b. New prefill tokens U = P - R by source (share of U; mean U per run)

| step | arm | U per run | block tail | re-prefill after edits | task + system | tool outputs | own turns | other (nudges, notices, summaries, template) | re-prefill in summary calls |
|---|---|---|---|---|---|---|---|---|---|
| step 9 | clm-32k | 18,153 | 0.6% | 3.7% | 15.2% | 35.9% | 33.1% | 11.5% | 0.0% |
| step 9 | clm-full | 18,532 | 0.5% | 0.0% | 14.9% | 41.7% | 29.3% | 13.6% | 0.0% |
| step 9 | econo+jev | 16,973 | 0.7% | 0.0% | 10.4% | 48.3% | 39.8% | 0.8% | 0.0% |
| step 9 | raw | 17,376 | 0.6% | 0.0% | 10.1% | 50.7% | 38.1% | 0.5% | 0.0% |
| 10d | clm-32k | 18,006 | 0.5% | 0.1% | 14.8% | 25.8% | 41.8% | 17.0% | 0.0% |
| 10d | raw-32k | 16,117 | 0.9% | 1.3% | 10.4% | 49.3% | 37.0% | 1.1% | 0.0% |
| 10d | summary-32k | 34,398 | 0.6% | 33.5% | 4.9% | 29.5% | 28.0% | 3.6% | 97.4% |
| step 11 | base-32k | 19,762 | 0.9% | 1.0% | 13.4% | 46.5% | 37.3% | 1.0% | 0.0% |
| step 11 | clm-32k | 23,825 | 1.7% | 1.0% | 15.5% | 32.5% | 46.5% | 2.8% | 0.0% |
| step 11 | summary-32k | 106,454 | 0.5% | 41.6% | 2.4% | 30.8% | 20.9% | 3.8% | 100.0% |

## 2. Overlap: tool-output lines already visible when the output arrived

| step | arm | outputs | output tokens | visible lines (8+ chars) | visible lines (all non-blank) | tokens saved showing only new lines | share of output tokens | outputs with any saving |
|---|---|---|---|---|---|---|---|---|
| step 9 | clm-32k | 392 | 185,765 | 17.3% | 20.9% | 13,847 | 7.5% | 342 |
| step 9 | clm-full | 398 | 221,338 | 16.7% | 18.9% | 18,919 | 8.5% | 355 |
| step 9 | econo+jev | 571 | 240,992 | 23.2% | 25.4% | 28,072 | 11.6% | 511 |
| step 9 | raw | 465 | 260,453 | 19.9% | 22.8% | 27,993 | 10.7% | 417 |
| 10d | clm-32k | 20 | 8,776 | 21.6% | 20.3% | 718 | 8.2% | 18 |
| 10d | raw-32k | 32 | 15,628 | 13.5% | 16.8% | 693 | 4.4% | 30 |
| 10d | summary-32k | 45 | 19,927 | 11.2% | 13.2% | 926 | 4.6% | 41 |
| step 11 | base-32k | 49 | 17,926 | 19.2% | 26.2% | 1,823 | 10.2% | 43 |
| step 11 | clm-32k | 49 | 6,442 | 17.2% | 21.3% | 496 | 7.7% | 47 |
| step 11 | summary-32k | 59 | 32,241 | 17.3% | 17.1% | 3,520 | 10.9% | 49 |

## 3. Later use of big outputs (1,000+ tokens as shown)

| step | arm | big outputs | their tokens | never used again | tokens never used again | quoted (30+ chars) | source path named | a new path from it named |
|---|---|---|---|---|---|---|---|---|
| step 9 | clm-32k | 41 | 106,498 | 4 (9.8%) | 8,275 (7.8%) | 39.0% | 65.9% | 46.3% |
| step 9 | clm-full | 52 | 134,617 | 7 (13.5%) | 16,689 (12.4%) | 38.5% | 78.8% | 36.5% |
| step 9 | econo+jev | 62 | 127,299 | 4 (6.5%) | 8,757 (6.9%) | 45.2% | 71.0% | 35.5% |
| step 9 | raw | 58 | 166,804 | 10 (17.2%) | 21,164 (12.7%) | 36.2% | 62.1% | 32.8% |
| 10d | clm-32k | 3 | 4,534 | 0 (0.0%) | 0 (0.0%) | 0.0% | 100.0% | 0.0% |
| 10d | raw-32k | 4 | 6,574 | 1 (25.0%) | 1,207 (18.4%) | 0.0% | 75.0% | 0.0% |
| 10d | summary-32k | 5 | 7,467 | 0 (0.0%) | 0 (0.0%) | 20.0% | 80.0% | 20.0% |
| step 11 | base-32k | 6 | 8,856 | 0 (0.0%) | 0 (0.0%) | 33.3% | 100.0% | 33.3% |
| step 11 | clm-32k | 0 | 0 | 0 (–) | 0 (–) | – | – | – |
| step 11 | summary-32k | 7 | 14,525 | 0 (0.0%) | 0 (0.0%) | 28.6% | 85.7% | 14.3% |

## 4. Loops (2+ consecutive identical tool calls, or identical error outputs)

| step | arm | agent calls | command loops / calls in them | error loops / calls in them | extra calls (after the first in a loop) | PFLOPs of the extra calls |
|---|---|---|---|---|---|---|
| step 9 | clm-32k | 423 | 0 / 0 (0 cut) | 0 / 0 | 0 | 0.000 (0.0%) |
| step 9 | clm-full | 430 | 0 / 0 (0 cut) | 0 / 0 | 0 | 0.000 (0.0%) |
| step 9 | econo+jev | 553 | 0 / 0 (0 cut) | 1 / 2 | 1 | 0.015 (0.0%) |
| step 9 | raw | 468 | 0 / 0 (0 cut) | 1 / 3 | 2 | 0.103 (0.2%) |
| 10d | clm-32k | 23 | 0 / 0 (0 cut) | 0 / 0 | 0 | 0.000 (0.0%) |
| 10d | raw-32k | 49 | 3 / 7 (7 cut) | 0 / 0 | 4 | 0.474 (8.5%) |
| 10d | summary-32k | 68 | 1 / 2 (2 cut) | 0 / 0 | 1 | 0.115 (1.3%) |
| step 11 | base-32k | 60 | 1 / 2 (2 cut) | 0 / 0 | 1 | 0.111 (2.2%) |
| step 11 | clm-32k | 58 | 2 / 7 (7 cut) | 0 / 0 | 5 | 0.550 (13.2%) |
| step 11 | summary-32k | 58 | 0 / 0 (0 cut) | 0 / 0 | 0 | 0.000 (0.0%) |

Every loop:
- tc9.econo+jev.bandit-delayed-feedback.r2: repeat_error from call 22, 2 calls (0 cut at the output cap); calls [["bash", "{\"command\": \"cd /app && timeout 900 python3 validate4.py\"}"]]
- tc9.raw.bandit-delayed-feedback.r3: repeat_error from call 17, 3 calls (0 cut at the output cap); calls [["bash", "{\"command\": \"cd /app && python3 - << 'EOF'\\n# patch solution: replace feat usage with explicit arm dummy construction\\nimport re\\nsrc = open('s
- tc10s.raw-32k.bandit-delayed-feedback.r1: repeat_command from call 22, 2 calls (2 cut at the output cap); calls [["bash", "{}"]]
- tc10s.raw-32k.bandit-delayed-feedback.r1: repeat_command from call 25, 2 calls (2 cut at the output cap); calls [["bash", "{}"]]
- tc10s.raw-32k.bandit-delayed-feedback.r1: repeat_command from call 29, 3 calls (3 cut at the output cap); calls [["bash", "{}"]]
- tc10s.summary-32k.bandit-delayed-feedback.r1: repeat_command from call 48, 2 calls (2 cut at the output cap); calls [["bash", "{}"]]
- tc11v.base-32k.scan-linux-persistence-artifacts.r1: repeat_command from call 17, 2 calls (2 cut at the output cap); calls [["write_file", "{\"path\": \"/app/scan_persistence.sh\"}"]]
- tc11v.clm-32k.scan-linux-persistence-artifacts.r1: repeat_command from call 9, 2 calls (2 cut at the output cap); calls [["bash", "{}"]]
- tc11v.clm-32k.scan-linux-persistence-artifacts.r1: repeat_command from call 13, 5 calls (5 cut at the output cap); calls [["bash", "{}"]]

## 5. Thinking tokens against prompt size P (agent calls; correlation within each run of 5+ calls)

| step | arm | calls | mean thinking tokens | thinking share of G | runs with r | median Pearson r | median Spearman | runs r > 0 | runs r > 0.3, r < -0.3 |
|---|---|---|---|---|---|---|---|---|---|
| step 9 | clm-32k | 423 | 407 | 0.40 | 28 | +0.20 | +0.38 | 19/28 | 8/28, 2/28 |
| step 9 | clm-full | 430 | 496 | 0.42 | 29 | +0.04 | +0.34 | 16/29 | 11/29, 4/29 |
| step 9 | econo+jev | 553 | 657 | 0.43 | 28 | +0.06 | +0.20 | 19/28 | 7/28, 3/28 |
| step 9 | raw | 468 | 816 | 0.41 | 28 | +0.03 | +0.23 | 15/28 | 8/28, 7/28 |
| 10d | clm-32k | 23 | 401 | 0.43 | 2 | +0.08 | +0.34 | 1/2 | 1/2, 0/2 |
| 10d | raw-32k | 49 | 964 | 0.57 | 1 | +0.01 | -0.07 | 1/1 | 0/1, 0/1 |
| 10d | summary-32k | 68 | 1060 | 0.62 | 1 | +0.33 | +0.24 | 1/1 | 1/1, 0/1 |
| step 11 | base-32k | 60 | 616 | 0.50 | 2 | -0.26 | -0.15 | 0/2 | 0/2, 0/2 |
| step 11 | clm-32k | 58 | 460 | 0.43 | 1 | +0.10 | +0.40 | 1/1 | 0/1, 0/1 |
| step 11 | summary-32k | 58 | 371 | 0.38 | 1 | +0.03 | +0.05 | 1/1 | 0/1, 0/1 |
