# Step 16 tables (fixed-trajectory estimates, canonical track)

## Waste as a share of each group's total FLOPs

| category | TBLite: CLM | TBLite: our harness, no context management | BrowseComp-Plus pilot: CLM | BrowseComp-Plus pilot: our harness, no context management |
|---|---|---|---|---|
| (a) repeated actions, identical result | 0.0% | 0.2% | 0.0% | 0.0% |
|   earlier result still visible (model behaviour) | 0.0% | 0.2% | 0.0% | 0.0% |
|   earlier result removed by CLM's edit (context manager) | 0.0% | 0.0% | 0.0% | 0.0% |
|   earlier result removed by CLM's rollback (context manager) | 0.0% | 0.0% | 0.0% | 0.0% |
| (b) work redone after a rollback (context manager) | 0.0% | 0.0% | 0.0% | 0.0% |
| (c) turns after sufficiency | 0.2% | 0.2% | 10.5% | 11.0% |
| (d) runs ended without an answer | 0.0% | 22.4% | 0.0% | 80.1% |
| (e) unreferenced observations | 2.2% | 2.5% | 7.5% | 31.8% |
| **overlap-free total, (a)-(e)** | 2.3% | 24.7% | 17.8% | 91.2% |
| (f) replies cut at the output cap, thinking only (not in your list) | 1.1% | 9.8% | 21.6% | 0.0% |
| **overlap-free total, (a)-(f)** | 3.4% | 33.1% | 39.1% | 91.2% |
| sensitivity: rephrased redo after a rollback | 0.0% | 0.0% | 2.6% | 0.0% |
| runs; PFLOPs | 63; 100.7 | 34; 73.4 | 8; 106.6 | 4; 6.3 |

## TBLite, CLM: 63 runs, 934 calls, 100.7 PFLOPs

Piece model vs the real prompts: 98.6% of the tokens.

### Waterfall (parts sum to 100%)

| part | share of FLOPs |
|---|---|
| gen: task | 33.2% |
| gen: management | 1.2% |
| gen: none | 1.0% |
| prefill: system and task | 8.4% |
| prefill: command outputs | 20.9% |
| prefill: management outputs | 0.2% |
| prefill: re-prefill after edits and rollbacks | 7.6% |
| prefill: reasoning made visible | 2.6% |
| prefill: text written by edits | 0.6% |
| prefill: own turns and harness messages | 14.7% |
| prefill: cache rounding | 0.3% |
| attention: prefill | 5.1% |
| attention: generation | 4.0% |
| (sum) | 100.0% |

### Counts

- (a) repeats of the same normalized action: 2; identical result 1 (visible 1, removed by edit 0, removed by rollback 0); result changed (not counted) 1.
- (b) rollbacks 0; exact redos within 5 calls 0; rephrased redos (sensitivity) 0.
- (c) runs with a sufficiency point: 57 of 63; calls after it: 3 (sufficiency calls: tc9.clm-32k.maven-slf4j-conflict.r1 at 24 of 26, tc9.clm-full.bandit-delayed-feedback.r2 at 28 of 30, tc9.clm-full.malicious-package-forensics.r1 at 24 of 26).
- (d) runs without an answer: 0 of 63.
- (e) unreferenced observations: 239 of 840 outputs, 8.1% of observation tokens.
- (f) replies cut at the output cap with thinking only: 9 calls, 18,432 generated tokens.

## TBLite, our harness, no context management: 34 runs, 577 calls, 73.4 PFLOPs

Piece model vs the real prompts: 99.0% of the tokens.

### Waterfall (parts sum to 100%)

| part | share of FLOPs |
|---|---|
| gen: task | 38.8% |
| gen: none | 9.1% |
| prefill: system and task | 4.1% |
| prefill: command outputs | 19.5% |
| prefill: own turns and harness messages | 15.1% |
| prefill: cache rounding | 0.3% |
| attention: prefill | 5.3% |
| attention: generation | 7.8% |
| (sum) | 100.0% |

### Counts

- (a) repeats of the same normalized action: 3; identical result 2 (visible 2, removed by edit 0, removed by rollback 0); result changed (not counted) 1.
- (b) rollbacks 0; exact redos within 5 calls 0; rephrased redos (sensitivity) 0.
- (c) runs with a sufficiency point: 27 of 34; calls after it: 3 (sufficiency calls: tc9.raw.malicious-package-forensics.r3 at 9 of 11, tc9.raw.maven-slf4j-conflict.r3 at 17 of 19, tc9.raw.sales-data-csv-analysis.r2 at 8 of 10).
- (d) runs without an answer: 2 of 34.
- (e) unreferenced observations: 173 of 546 outputs, 8.5% of observation tokens.
- (f) replies cut at the output cap with thinking only: 36 calls, 129,024 generated tokens.

## BrowseComp-Plus pilot, CLM: 8 runs, 513 calls, 106.6 PFLOPs

Piece model vs the real prompts: 98.7% of the tokens.

### Waterfall (parts sum to 100%)

| part | share of FLOPs |
|---|---|
| gen: task | 14.9% |
| gen: management | 2.6% |
| gen: none | 13.4% |
| prefill: system and task | 0.6% |
| prefill: search results | 23.3% |
| prefill: documents | 1.3% |
| prefill: command outputs | 0.0% |
| prefill: management outputs | 0.9% |
| prefill: re-prefill after edits and rollbacks | 21.3% |
| prefill: reasoning made visible | 6.8% |
| prefill: text written by edits | 0.9% |
| prefill: own turns and harness messages | 5.7% |
| prefill: cache rounding | 0.1% |
| attention: prefill | 4.9% |
| attention: generation | 3.4% |
| (sum) | 100.0% |

### Counts

- (a) repeats of the same normalized action: 2; identical result 0 (visible 0, removed by edit 0, removed by rollback 0); result changed (not counted) 2.
- (b) rollbacks 30; exact redos within 5 calls 0; rephrased redos (sensitivity) 5.
- (c) runs with a sufficiency point: 3 of 8; calls after it: 36 (sufficiency calls: 1226 at 1 of 30, 600 at 6 of 12, 741 at 3 of 7).
- (d) runs without an answer: 0 of 8.
- (e) unreferenced observations: 123 of 323 outputs, 18.7% of observation tokens; per document inside search results: 51.6% of snippet tokens unreferenced.
- (f) replies cut at the output cap with thinking only: 68 calls, 278,528 generated tokens.

## BrowseComp-Plus pilot, our harness, no context management: 4 runs, 20 calls, 6.3 PFLOPs

Piece model vs the real prompts: 99.5% of the tokens.

### Waterfall (parts sum to 100%)

| part | share of FLOPs |
|---|---|
| gen: task | 13.7% |
| gen: none | 3.1% |
| prefill: system and task | 3.3% |
| prefill: search results | 67.1% |
| prefill: documents | 2.0% |
| prefill: own turns and harness messages | 1.0% |
| prefill: cache rounding | 0.1% |
| attention: prefill | 7.1% |
| attention: generation | 2.6% |
| (sum) | 100.0% |

### Counts

- (a) repeats of the same normalized action: 0; identical result 0 (visible 0, removed by edit 0, removed by rollback 0); result changed (not counted) 0.
- (b) rollbacks 0; exact redos within 5 calls 0; rephrased redos (sensitivity) 0.
- (c) runs with a sufficiency point: 1 of 4; calls after it: 2 (sufficiency calls: 741 at 2 of 5).
- (d) runs without an answer: 3 of 4.
- (e) unreferenced observations: 12 of 22 outputs, 39.0% of observation tokens; per document inside search results: 89.4% of snippet tokens unreferenced.
- (f) replies cut at the output cap with thinking only: 0 calls, 0 generated tokens.
