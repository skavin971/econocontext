# Step 12c tables

130 runs. Shares of FLOPs are of the arm's total (Eq. 9, every call).


## 1. The model's own turns, prefilled again: tokens (share of the arm's FLOPs, linear + prefill attention)

| step | arm | runs | own-turn tokens | their FLOPs | visible text | write_file content | bash command (outside heredocs) | bash heredoc bodies | other arguments | template glue |
|---|---|---|---|---|---|---|---|---|---|---|
| step 9 | clm-32k | 30 | 183,499 | 21.5% | 50,896 (6.0%) | 0 (0.0%) | 21,054 (2.4%) | 99,372 (11.6%) | 0 (0.0%) | 12,177 (1.4%) |
| step 9 | clm-full | 30 | 162,968 | 18.3% | 44,075 (5.1%) | 0 (0.0%) | 24,625 (2.7%) | 81,981 (9.2%) | 0 (0.0%) | 12,287 (1.3%) |
| step 9 | econo+jev | 30 | 202,753 | 18.9% | 6,299 (0.6%) | 66,470 (6.1%) | 29,615 (2.7%) | 83,229 (8.0%) | 261 (0.0%) | 16,879 (1.5%) |
| step 9 | raw | 30 | 198,389 | 17.9% | 5,036 (0.5%) | 68,909 (6.0%) | 24,426 (2.1%) | 86,142 (8.1%) | 194 (0.0%) | 13,682 (1.2%) |
| 10d | clm-32k | 2 | 15,065 | 29.6% | 9,065 (17.8%) | 0 (0.0%) | 1,218 (2.4%) | 4,093 (8.1%) | 0 (0.0%) | 689 (1.3%) |
| 10d | raw-32k | 2 | 12,007 | 12.1% | 84 (0.1%) | 0 (0.0%) | 703 (0.7%) | 10,290 (10.4%) | 0 (0.0%) | 930 (0.9%) |
| 10d | summary-32k | 2 | 30,453 | 18.6% | 436 (0.3%) | 0 (0.0%) | 1,163 (0.7%) | 26,752 (16.4%) | 4 (0.0%) | 2,098 (1.3%) |
| step 11 | base-32k | 2 | 14,800 | 15.5% | 181 (0.2%) | 3,114 (3.2%) | 2,139 (2.2%) | 7,858 (8.2%) | 24 (0.0%) | 1,484 (1.5%) |
| step 11 | clm-32k | 1 | 11,121 | 14.4% | 644 (0.9%) | 0 (0.0%) | 2,868 (3.8%) | 6,203 (8.0%) | 0 (0.0%) | 1,406 (1.8%) |
| step 11 | summary-32k | 1 | 39,265 | 25.4% | 664 (0.4%) | 3,092 (2.1%) | 9,072 (5.7%) | 23,244 (15.1%) | 32 (0.0%) | 3,161 (2.0%) |

## 2. The free edit zone: the previous reply and the latest tool outputs in U

| step | arm | previous replies | wholly in U | body in U, header in R | body in R | tool outputs | in U | not in U | calls with no previous reply |
|---|---|---|---|---|---|---|---|---|---|
| step 9 | clm-32k | 393 | 298 | 94 | 1 | 392 | 392 | 0 | 30 |
| step 9 | clm-full | 400 | 305 | 95 | 0 | 398 | 398 | 0 | 30 |
| step 9 | econo+jev | 523 | 376 | 147 | 0 | 571 | 571 | 0 | 31 |
| step 9 | raw | 438 | 320 | 118 | 0 | 465 | 465 | 0 | 30 |
| 10d | clm-32k | 21 | 18 | 3 | 0 | 20 | 20 | 0 | 2 |
| 10d | raw-32k | 47 | 32 | 15 | 0 | 32 | 32 | 0 | 2 |
| 10d | summary-32k | 66 | 43 | 23 | 0 | 45 | 45 | 0 | 3 |
| step 11 | base-32k | 58 | 37 | 21 | 0 | 49 | 49 | 0 | 2 |
| step 11 | clm-32k | 57 | 41 | 16 | 0 | 49 | 49 | 0 | 1 |
| step 11 | summary-32k | 57 | 47 | 10 | 0 | 59 | 59 | 0 | 3 |

Exceptions with a body token in R: tc9.clm-32k.scan-linux-persistence-artifacts.r1 call 17 (R 3696, body starts at token 3695)

## 3a. The slicing upper bound: big outputs (1,000+ tokens), 40-line chunks, the oracle slice

| step | arm | big outputs | tokens | chunks | later-used chunks | outputs with a used chunk | used by (a chunk may count twice) | oracle slice saves | BM25 query fell back to the call's arguments |
|---|---|---|---|---|---|---|---|---|---|
| step 9 | clm-32k | 41 | 106,498 | 117 (6 outputs with 1) | 53 (45.3%) | 23 | quoted 25, line 2, symbol 41 | 46,567 (43.7%) | 10 |
| step 9 | clm-full | 52 | 134,617 | 136 (13 outputs with 1) | 53 (39.0%) | 28 | quoted 32, line 6, symbol 39 | 66,691 (49.5%) | 15 |
| step 9 | econo+jev | 62 | 127,299 | 131 (15 outputs with 1) | 46 (35.1%) | 32 | quoted 34, line 0, symbol 35 | 52,861 (41.5%) | 39 |
| step 9 | raw | 58 | 166,804 | 181 (8 outputs with 1) | 62 (34.3%) | 26 | quoted 50, line 0, symbol 40 | 83,126 (49.8%) | 35 |
| 10d | clm-32k | 3 | 4,534 | 6 (1 outputs with 1) | 0 (0.0%) | 0 | quoted 0, line 0, symbol 0 | 4,534 (100.0%) | 3 |
| 10d | raw-32k | 4 | 6,574 | 7 (2 outputs with 1) | 0 (0.0%) | 0 | quoted 0, line 0, symbol 0 | 6,574 (100.0%) | 3 |
| 10d | summary-32k | 5 | 7,467 | 15 (1 outputs with 1) | 7 (46.7%) | 3 | quoted 4, line 0, symbol 7 | 3,431 (45.9%) | 4 |
| step 11 | base-32k | 6 | 8,856 | 15 (1 outputs with 1) | 4 (26.7%) | 2 | quoted 4, line 0, symbol 2 | 6,792 (76.7%) | 6 |
| step 11 | clm-32k | 0 | – | – | – | – | – | – | – |
| step 11 | summary-32k | 7 | 14,525 | 12 (5 outputs with 1) | 6 (50.0%) | 2 | quoted 6, line 4, symbol 6 | 11,037 (76.0%) | 2 |

## 3b. BM25 slices (top chunks against the agent's last visible text): tokens saved; later-used chunks dropped

| step | arm | later-used chunks | keep up to 25% | keep up to 50% | keep up to 75% |
|---|---|---|---|---|---|
| step 9 | clm-32k | 53 | 95.4%; missed 46 (23 outputs) | 80.8%; missed 36 (21 outputs) | 59.6%; missed 27 (20 outputs) |
| step 9 | clm-full | 53 | 96.3%; missed 50 (28 outputs) | 82.0%; missed 41 (25 outputs) | 58.2%; missed 28 (23 outputs) |
| step 9 | econo+jev | 46 | 99.6%; missed 45 (31 outputs) | 88.9%; missed 41 (31 outputs) | 80.6%; missed 36 (30 outputs) |
| step 9 | raw | 62 | 94.7%; missed 59 (26 outputs) | 74.7%; missed 47 (25 outputs) | 57.5%; missed 32 (21 outputs) |
| 10d | clm-32k | 0 | 96.2%; missed 0 (0 outputs) | 87.7%; missed 0 (0 outputs) | 87.7%; missed 0 (0 outputs) |
| 10d | raw-32k | 0 | 100.0%; missed 0 (0 outputs) | 100.0%; missed 0 (0 outputs) | 87.0%; missed 0 (0 outputs) |
| 10d | summary-32k | 7 | 91.9%; missed 6 (3 outputs) | 68.2%; missed 4 (3 outputs) | 49.9%; missed 3 (3 outputs) |
| step 11 | base-32k | 4 | 94.3%; missed 4 (2 outputs) | 80.8%; missed 3 (2 outputs) | 45.9%; missed 2 (2 outputs) |
| step 11 | summary-32k | 6 | 100.0%; missed 6 (2 outputs) | 89.8%; missed 3 (2 outputs) | 86.2%; missed 2 (2 outputs) |
