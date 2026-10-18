# Step 12d tables

238 big outputs, 2275 chunks of about 300 tokens (677 later used, in 116 outputs). Jev: {'ok': 238}; Jev's slicer is scored on the 238 outputs it answered or that have one chunk.

Jev requests to TypeSafe: 238, failed 0; input tokens 3,841,125, output tokens 39,065; seconds per request: median 0.27.

## Overall

238 outputs, 577,174 tokens, 677 later-used chunks. Each cell: tokens saved (share of the outputs' tokens); recall (later-used chunks kept).

| slicer | keep ≤ 25% | keep ≤ 50% | keep ≤ 75% |
|---|---|---|---|
| head_tail | 78.7%; recall 20.8% | 55.1%; recall 45.6% | 30.6%; recall 70.8% |
| bm25_reply | 78.4%; recall 20.8% | 53.6%; recall 46.8% | 29.3%; recall 69.7% |
| bm25_task | 78.6%; recall 23.3% | 53.5%; recall 50.1% | 29.5%; recall 74.0% |
| jev | 78.6%; recall 27.8% | 53.8%; recall 50.8% | 29.7%; recall 72.8% |
| oracle | 88.2%; recall 39.0% | 78.5%; recall 70.2% | 72.9%; recall 87.6% |
| oracle, no budget | 69.2%; recall 100% (all three columns) |

## Overall, on the outputs Jev was scored on

238 outputs, 577,174 tokens, 677 later-used chunks. Each cell: tokens saved (share of the outputs' tokens); recall (later-used chunks kept).

| slicer | keep ≤ 25% | keep ≤ 50% | keep ≤ 75% |
|---|---|---|---|
| head_tail | 78.7%; recall 20.8% | 55.1%; recall 45.6% | 30.6%; recall 70.8% |
| bm25_reply | 78.4%; recall 20.8% | 53.6%; recall 46.8% | 29.3%; recall 69.7% |
| bm25_task | 78.6%; recall 23.3% | 53.5%; recall 50.1% | 29.5%; recall 74.0% |
| jev | 78.6%; recall 27.8% | 53.8%; recall 50.8% | 29.7%; recall 72.8% |
| oracle | 88.2%; recall 39.0% | 78.5%; recall 70.2% | 72.9%; recall 87.6% |
| oracle, no budget | 69.2%; recall 100% (all three columns) |

## step 9, clm-32k

41 outputs, 106,498 tokens, 130 later-used chunks. Each cell: tokens saved (share of the outputs' tokens); recall (later-used chunks kept).

| slicer | keep ≤ 25% | keep ≤ 50% | keep ≤ 75% |
|---|---|---|---|
| head_tail | 78.5%; recall 21.5% | 54.9%; recall 44.6% | 29.4%; recall 70.8% |
| bm25_reply | 77.8%; recall 21.5% | 53.1%; recall 53.8% | 29.2%; recall 73.1% |
| bm25_task | 77.9%; recall 24.6% | 53.3%; recall 53.8% | 29.4%; recall 74.6% |
| jev | 78.1%; recall 26.9% | 53.2%; recall 53.8% | 29.0%; recall 77.7% |
| oracle | 87.3%; recall 40.0% | 76.4%; recall 72.3% | 69.8%; recall 92.3% |
| oracle, no budget | 67.4%; recall 100% (all three columns) |

## step 9, clm-full

52 outputs, 134,617 tokens, 146 later-used chunks. Each cell: tokens saved (share of the outputs' tokens); recall (later-used chunks kept).

| slicer | keep ≤ 25% | keep ≤ 50% | keep ≤ 75% |
|---|---|---|---|
| head_tail | 79.4%; recall 17.1% | 55.1%; recall 42.5% | 30.5%; recall 69.2% |
| bm25_reply | 78.8%; recall 21.9% | 53.3%; recall 47.9% | 29.1%; recall 73.3% |
| bm25_task | 79.2%; recall 24.0% | 53.6%; recall 52.1% | 29.4%; recall 77.4% |
| jev | 78.7%; recall 31.5% | 53.6%; recall 53.4% | 29.5%; recall 76.0% |
| oracle | 88.0%; recall 42.5% | 79.6%; recall 70.5% | 73.6%; recall 89.7% |
| oracle, no budget | 70.5%; recall 100% (all three columns) |

## step 9, econo+jev

62 outputs, 127,299 tokens, 149 later-used chunks. Each cell: tokens saved (share of the outputs' tokens); recall (later-used chunks kept).

| slicer | keep ≤ 25% | keep ≤ 50% | keep ≤ 75% |
|---|---|---|---|
| head_tail | 77.9%; recall 20.8% | 55.4%; recall 45.0% | 31.5%; recall 67.8% |
| bm25_reply | 78.0%; recall 20.8% | 53.9%; recall 45.6% | 29.1%; recall 67.8% |
| bm25_task | 78.0%; recall 23.5% | 53.3%; recall 48.3% | 29.5%; recall 72.5% |
| jev | 78.1%; recall 28.9% | 53.8%; recall 51.7% | 29.5%; recall 71.8% |
| oracle | 88.1%; recall 40.3% | 78.6%; recall 72.5% | 74.0%; recall 86.6% |
| oracle, no budget | 70.2%; recall 100% (all three columns) |

## step 9, raw

58 outputs, 166,804 tokens, 222 later-used chunks. Each cell: tokens saved (share of the outputs' tokens); recall (later-used chunks kept).

| slicer | keep ≤ 25% | keep ≤ 50% | keep ≤ 75% |
|---|---|---|---|
| head_tail | 78.7%; recall 23.0% | 54.5%; recall 48.2% | 30.0%; recall 74.8% |
| bm25_reply | 78.7%; recall 19.8% | 53.6%; recall 43.2% | 29.4%; recall 67.1% |
| bm25_task | 78.8%; recall 22.1% | 53.5%; recall 47.7% | 29.4%; recall 72.5% |
| jev | 79.0%; recall 26.1% | 54.0%; recall 47.7% | 29.9%; recall 69.4% |
| oracle | 87.7%; recall 36.0% | 76.4%; recall 68.5% | 70.7%; recall 84.7% |
| oracle, no budget | 65.7%; recall 100% (all three columns) |

## 10d, clm-32k

3 outputs, 4,534 tokens, 0 later-used chunks. Each cell: tokens saved (share of the outputs' tokens); recall (later-used chunks kept).

| slicer | keep ≤ 25% | keep ≤ 50% | keep ≤ 75% |
|---|---|---|---|
| head_tail | 81.2%; recall – | 57.2%; recall – | 32.4%; recall – |
| bm25_reply | 80.3%; recall – | 61.1%; recall – | 31.0%; recall – |
| bm25_task | 80.5%; recall – | 56.0%; recall – | 31.3%; recall – |
| jev | 80.1%; recall – | 55.6%; recall – | 31.2%; recall – |
| oracle | 100.0%; recall – | 100.0%; recall – | 100.0%; recall – |
| oracle, no budget | 100.0%; recall 100% (all three columns) |

## 10d, raw-32k

4 outputs, 6,574 tokens, 0 later-used chunks. Each cell: tokens saved (share of the outputs' tokens); recall (later-used chunks kept).

| slicer | keep ≤ 25% | keep ≤ 50% | keep ≤ 75% |
|---|---|---|---|
| head_tail | 78.6%; recall – | 57.8%; recall – | 33.6%; recall – |
| bm25_reply | 78.6%; recall – | 54.1%; recall – | 29.9%; recall – |
| bm25_task | 78.6%; recall – | 54.0%; recall – | 29.8%; recall – |
| jev | 79.8%; recall – | 54.0%; recall – | 33.6%; recall – |
| oracle | 100.0%; recall – | 100.0%; recall – | 100.0%; recall – |
| oracle, no budget | 100.0%; recall 100% (all three columns) |

## 10d, summary-32k

5 outputs, 7,467 tokens, 12 later-used chunks. Each cell: tokens saved (share of the outputs' tokens); recall (later-used chunks kept).

| slicer | keep ≤ 25% | keep ≤ 50% | keep ≤ 75% |
|---|---|---|---|
| head_tail | 80.2%; recall 16.7% | 57.9%; recall 50.0% | 34.7%; recall 66.7% |
| bm25_reply | 77.1%; recall 16.7% | 53.7%; recall 41.7% | 28.0%; recall 66.7% |
| bm25_task | 77.6%; recall 25.0% | 53.8%; recall 50.0% | 27.9%; recall 75.0% |
| jev | 78.0%; recall 25.0% | 55.5%; recall 41.7% | 32.4%; recall 58.3% |
| oracle | 87.2%; recall 33.3% | 73.9%; recall 66.7% | 66.3%; recall 83.3% |
| oracle, no budget | 58.4%; recall 100% (all three columns) |

## step 11, base-32k

6 outputs, 8,856 tokens, 6 later-used chunks. Each cell: tokens saved (share of the outputs' tokens); recall (later-used chunks kept).

| slicer | keep ≤ 25% | keep ≤ 50% | keep ≤ 75% |
|---|---|---|---|
| head_tail | 79.2%; recall 16.7% | 55.8%; recall 50.0% | 36.4%; recall 50.0% |
| bm25_reply | 78.3%; recall 16.7% | 53.1%; recall 33.3% | 30.3%; recall 50.0% |
| bm25_task | 79.0%; recall 16.7% | 53.0%; recall 50.0% | 32.8%; recall 50.0% |
| jev | 78.9%; recall 16.7% | 55.7%; recall 33.3% | 32.6%; recall 66.7% |
| oracle | 90.0%; recall 50.0% | 86.7%; recall 66.7% | 83.6%; recall 83.3% |
| oracle, no budget | 80.4%; recall 100% (all three columns) |

## step 11, summary-32k

7 outputs, 14,525 tokens, 12 later-used chunks. Each cell: tokens saved (share of the outputs' tokens); recall (later-used chunks kept).

| slicer | keep ≤ 25% | keep ≤ 50% | keep ≤ 75% |
|---|---|---|---|
| head_tail | 80.3%; recall 25.0% | 55.4%; recall 50.0% | 30.8%; recall 66.7% |
| bm25_reply | 80.1%; recall 25.0% | 56.0%; recall 50.0% | 31.1%; recall 75.0% |
| bm25_task | 80.0%; recall 25.0% | 55.9%; recall 50.0% | 31.0%; recall 83.3% |
| jev | 79.5%; recall 16.7% | 55.6%; recall 50.0% | 31.1%; recall 75.0% |
| oracle | 95.6%; recall 25.0% | 89.9%; recall 50.0% | 81.8%; recall 83.3% |
| oracle, no budget | 77.7%; recall 100% (all three columns) |

## FLOPs

The big outputs' share of each arm's total FLOPs (Eq. 9, every call), and the FLOPs each slicer saves: the prefill when an output is first computed plus the attention in every later call. **Assumption: no extra calls from misses** (a dropped later-used chunk is never fetched again).

| step | arm | runs | PFLOPs | big outputs' share | head_tail 25/50/75% | bm25_reply 25/50/75% | bm25_task 25/50/75% | jev 25/50/75% | oracle 25/50/75% | oracle, no budget |
|---|---|---|---|---|---|---|---|---|---|---|
| step 9 | clm-32k | 30 | 45.48 | 13.2% | 10.4% / 7.4% / 4.0% | 10.4% / 7.1% / 4.0% | 10.4% / 7.2% / 4.0% | 10.4% / 7.2% / 3.9% | 11.6% / 10.2% / 9.3% | 9.0% |
| step 9 | clm-full | 30 | 49.23 | 16.8% | 13.4% / 9.5% / 5.3% | 13.4% / 9.2% / 5.1% | 13.4% / 9.2% / 5.1% | 13.3% / 9.2% / 5.1% | 14.9% / 13.5% / 12.5% | 11.9% |
| step 9 | econo+jev | 30 | 60.08 | 13.5% | 10.6% / 7.6% / 4.4% | 10.6% / 7.4% / 4.1% | 10.6% / 7.4% / 4.1% | 10.7% / 7.4% / 4.1% | 12.0% / 10.7% / 10.1% | 9.6% |
| step 9 | raw | 30 | 63.13 | 18.7% | 14.9% / 10.4% / 5.8% | 14.9% / 10.3% / 5.7% | 14.9% / 10.3% / 5.7% | 15.0% / 10.4% / 5.8% | 16.5% / 14.4% / 13.3% | 12.3% |
| 10d | clm-32k | 2 | 2.70 | 9.8% | 8.0% / 5.6% / 3.2% | 7.9% / 6.0% / 3.1% | 7.9% / 5.5% / 3.1% | 7.9% / 5.5% / 3.1% | 9.8% / 9.8% / 9.8% | 9.8% |
| 10d | raw-32k | 2 | 5.55 | 9.5% | 7.5% / 5.5% / 3.2% | 7.5% / 5.2% / 2.9% | 7.5% / 5.2% / 2.9% | 7.6% / 5.2% / 3.2% | 9.5% / 9.5% / 9.5% | 9.5% |
| 10d | summary-32k | 2 | 8.90 | 8.6% | 6.9% / 5.0% / 3.1% | 6.7% / 4.6% / 2.4% | 6.7% / 4.7% / 2.4% | 6.7% / 4.8% / 2.8% | 7.6% / 6.8% / 6.3% | 5.8% |
| step 11 | base-32k | 2 | 5.11 | 10.7% | 8.5% / 6.0% / 4.0% | 8.4% / 5.7% / 3.2% | 8.5% / 5.7% / 3.6% | 8.5% / 6.0% / 3.5% | 9.6% / 9.2% / 8.9% | 8.5% |
| step 11 | clm-32k | 1 | 4.18 | 0.0% | 0.0% / 0.0% / 0.0% | 0.0% / 0.0% / 0.0% | 0.0% / 0.0% / 0.0% | 0.0% / 0.0% / 0.0% | 0.0% / 0.0% / 0.0% | 0.0% |
| step 11 | summary-32k | 1 | 8.41 | 16.8% | 13.5% / 9.3% / 5.1% | 13.4% / 9.4% / 5.2% | 13.4% / 9.4% / 5.1% | 13.4% / 9.3% / 5.1% | 16.1% / 15.2% / 14.0% | 13.1% |
| all | all | 130 | 252.75 | 14.9% | 11.9% / 8.4% / 4.7% | 11.8% / 8.2% / 4.5% | 11.8% / 8.2% / 4.5% | 11.8% / 8.2% / 4.6% | 13.3% / 11.8% / 11.0% | 10.4% |
