# Step 14b tables (fixed-trajectory estimates, canonical track)


## TBLite

**(1) E savings, after repairs: 0.00% of total FLOPs. (2) Beyond E (V diffs + P chunks): 0.49%. (3) CLM's plausibly avoidable management cost (retained text whose cache reuse its edits lost): 4.96% of CLM's FLOPs (an upper bound).**


### Runs without edits (our harness: raw, raw-32k, base-32k, base): 34 runs, 73.8 PFLOPs

| class | share of tool-output tokens (200+) | share with a visible earlier copy | PFLOPs saved | share of total FLOPs | share of new-prefill FLOPs | concentration |
|---|---|---|---|---|---|---|
| E (references) | 0.00% | – | 0.000 | **0.00%** | 0.00% | – |
| V (diffs, beyond E) | 0.00% | – | 0.000 | 0.00% | 0.00% | – |
| P (chunk pointers, beyond E and V) | 9.26% (shared spans 3.85%) | 78.64% of shared spans | 0.586 | 0.79% | 2.03% | 4 of 34 runs give 80% |
| N (not savable) | 0.00% | – | – | – | – | – |
| R? (not savable) | 0.00% | – | – | – | – | – |
| U | 90.74% | – | – | – | – | – |

Tool-output tokens in items of 200+: 272,274; under 200: 20,776. N: 0 tokens; a reference plus its changed fields would cost 0. References 0, diffs 0, chunk pointers 1,367; repairs 0 (0 tokens); unsupported 0.00% of candidate tokens. Potentially stale current-state evidence: 0.00% of agent calls (a V-class source, only an older version visible, the call's action touches it); calls with any such stale copy visible: 0.00% (0 tokens). Broader context (any file whose visible read predates a later write to it, V class or not): 3.29% of calls; with the action touching it 0.00%.

### Runs with edits or management (econo+jev, summary, CLM): 96 runs, 179.0 PFLOPs

| class | share of tool-output tokens (200+) | share with a visible earlier copy | PFLOPs saved | share of total FLOPs | share of new-prefill FLOPs | concentration |
|---|---|---|---|---|---|---|
| E (references) | 0.00% | – | 0.000 | **0.00%** | 0.00% | – |
| V (diffs, beyond E) | 0.66% | 100.00% | 0.000 | 0.00% | 0.00% | – |
| P (chunk pointers, beyond E and V) | 11.34% (shared spans 2.28%) | 66.57% of shared spans | 0.648 | 0.36% | 0.72% | 15 of 96 runs give 80% |
| N (not savable) | 0.00% | – | – | – | – | – |
| R? (not savable) | 0.00% | – | – | – | – | – |
| U | 88.00% | – | – | – | – | – |

Tool-output tokens in items of 200+: 662,642; under 200: 66,477. N: 0 tokens; a reference plus its changed fields would cost 0. References 0, diffs 0, chunk pointers 946; repairs 0 (0 tokens); unsupported 6.05% of candidate tokens. Potentially stale current-state evidence: 0.00% of agent calls (a V-class source, only an older version visible, the call's action touches it); calls with any such stale copy visible: 0.00% (0 tokens). Broader context (any file whose visible read predates a later write to it, V class or not): 8.37% of calls; with the action touching it 4.90%.

### arm base-32k: 2 runs, 5.1 PFLOPs

| class | share of tool-output tokens (200+) | share with a visible earlier copy | PFLOPs saved | share of total FLOPs | share of new-prefill FLOPs | concentration |
|---|---|---|---|---|---|---|
| E (references) | 0.00% | – | 0.000 | **0.00%** | 0.00% | – |
| V (diffs, beyond E) | 0.00% | – | 0.000 | 0.00% | 0.00% | – |
| P (chunk pointers, beyond E and V) | 4.70% (shared spans 1.91%) | 80.26% of shared spans | 0.014 | 0.28% | 0.74% | 1 of 2 runs give 80% |
| N (not savable) | 0.00% | – | – | – | – | – |
| R? (not savable) | 0.00% | – | – | – | – | – |
| U | 95.30% | – | – | – | – | – |

Tool-output tokens in items of 200+: 16,189; under 200: 1,673. N: 0 tokens; a reference plus its changed fields would cost 0. References 0, diffs 0, chunk pointers 12; repairs 0 (0 tokens); unsupported 0.00% of candidate tokens. Potentially stale current-state evidence: 0.00% of agent calls (a V-class source, only an older version visible, the call's action touches it); calls with any such stale copy visible: 0.00% (0 tokens). Broader context (any file whose visible read predates a later write to it, V class or not): 0.00% of calls; with the action touching it 0.00%.

### arm clm-32k: 33 runs, 52.3 PFLOPs

| class | share of tool-output tokens (200+) | share with a visible earlier copy | PFLOPs saved | share of total FLOPs | share of new-prefill FLOPs | concentration |
|---|---|---|---|---|---|---|
| E (references) | 0.00% | – | 0.000 | **0.00%** | 0.00% | – |
| V (diffs, beyond E) | 0.00% | – | 0.000 | 0.00% | 0.00% | – |
| P (chunk pointers, beyond E and V) | 11.24% (shared spans 1.20%) | 70.10% of shared spans | 0.084 | 0.16% | 0.29% | 5 of 33 runs give 80% |
| N (not savable) | 0.00% | – | – | – | – | – |
| R? (not savable) | 0.00% | – | – | – | – | – |
| U | 88.76% | – | – | – | – | – |

Tool-output tokens in items of 200+: 182,669; under 200: 25,970. N: 0 tokens; a reference plus its changed fields would cost 0. References 0, diffs 0, chunk pointers 106; repairs 0 (0 tokens); unsupported 22.94% of candidate tokens. Potentially stale current-state evidence: 0.00% of agent calls (a V-class source, only an older version visible, the call's action touches it); calls with any such stale copy visible: 0.00% (0 tokens). Broader context (any file whose visible read predates a later write to it, V class or not): 6.35% of calls; with the action touching it 3.17%.

### arm clm-full: 30 runs, 49.2 PFLOPs

| class | share of tool-output tokens (200+) | share with a visible earlier copy | PFLOPs saved | share of total FLOPs | share of new-prefill FLOPs | concentration |
|---|---|---|---|---|---|---|
| E (references) | 0.00% | – | 0.000 | **0.00%** | 0.00% | – |
| V (diffs, beyond E) | 2.06% | 100.00% | 0.000 | 0.00% | 0.00% | – |
| P (chunk pointers, beyond E and V) | 14.78% (shared spans 2.45%) | 60.31% of shared spans | 0.194 | 0.39% | 0.72% | 5 of 30 runs give 80% |
| N (not savable) | 0.00% | – | – | – | – | – |
| R? (not savable) | 0.00% | – | – | – | – | – |
| U | 83.16% | – | – | – | – | – |

Tool-output tokens in items of 200+: 211,236; under 200: 16,816. N: 0 tokens; a reference plus its changed fields would cost 0. References 0, diffs 0, chunk pointers 172; repairs 0 (0 tokens); unsupported 5.80% of candidate tokens. Potentially stale current-state evidence: 0.00% of agent calls (a V-class source, only an older version visible, the call's action touches it); calls with any such stale copy visible: 0.00% (0 tokens). Broader context (any file whose visible read predates a later write to it, V class or not): 15.35% of calls; with the action touching it 7.21%.

### arm econo+jev: 30 runs, 60.1 PFLOPs

| class | share of tool-output tokens (200+) | share with a visible earlier copy | PFLOPs saved | share of total FLOPs | share of new-prefill FLOPs | concentration |
|---|---|---|---|---|---|---|
| E (references) | 0.00% | – | 0.000 | **0.00%** | 0.00% | – |
| V (diffs, beyond E) | 0.00% | – | 0.000 | 0.00% | 0.00% | – |
| P (chunk pointers, beyond E and V) | 8.12% (shared spans 2.63%) | 78.47% of shared spans | 0.290 | 0.48% | 1.17% | 7 of 30 runs give 80% |
| N (not savable) | 0.00% | – | – | – | – | – |
| R? (not savable) | 0.00% | – | – | – | – | – |
| U | 91.88% | – | – | – | – | – |

Tool-output tokens in items of 200+: 220,112; under 200: 20,786. N: 0 tokens; a reference plus its changed fields would cost 0. References 0, diffs 0, chunk pointers 550; repairs 0 (0 tokens); unsupported 0.00% of candidate tokens. Potentially stale current-state evidence: 0.00% of agent calls (a V-class source, only an older version visible, the call's action touches it); calls with any such stale copy visible: 0.00% (0 tokens). Broader context (any file whose visible read predates a later write to it, V class or not): 5.79% of calls; with the action touching it 5.61%.

### arm raw: 30 runs, 63.1 PFLOPs

| class | share of tool-output tokens (200+) | share with a visible earlier copy | PFLOPs saved | share of total FLOPs | share of new-prefill FLOPs | concentration |
|---|---|---|---|---|---|---|
| E (references) | 0.00% | – | 0.000 | **0.00%** | 0.00% | – |
| V (diffs, beyond E) | 0.00% | – | 0.000 | 0.00% | 0.00% | – |
| P (chunk pointers, beyond E and V) | 10.01% (shared spans 4.20%) | 78.63% of shared spans | 0.570 | 0.90% | 2.25% | 4 of 30 runs give 80% |
| N (not savable) | 0.00% | – | – | – | – | – |
| R? (not savable) | 0.00% | – | – | – | – | – |
| U | 89.99% | – | – | – | – | – |

Tool-output tokens in items of 200+: 241,095; under 200: 18,501. N: 0 tokens; a reference plus its changed fields would cost 0. References 0, diffs 0, chunk pointers 1,354; repairs 0 (0 tokens); unsupported 0.00% of candidate tokens. Potentially stale current-state evidence: 0.00% of agent calls (a V-class source, only an older version visible, the call's action touches it); calls with any such stale copy visible: 0.00% (0 tokens). Broader context (any file whose visible read predates a later write to it, V class or not): 4.06% of calls; with the action touching it 0.00%.

### arm raw-32k: 2 runs, 5.6 PFLOPs

| class | share of tool-output tokens (200+) | share with a visible earlier copy | PFLOPs saved | share of total FLOPs | share of new-prefill FLOPs | concentration |
|---|---|---|---|---|---|---|
| E (references) | 0.00% | – | 0.000 | **0.00%** | 0.00% | – |
| V (diffs, beyond E) | 0.00% | – | 0.000 | 0.00% | 0.00% | – |
| P (chunk pointers, beyond E and V) | 2.13% (shared spans 0.29%) | 67.44% of shared spans | 0.001 | 0.03% | 0.09% | 1 of 2 runs give 80% |
| N (not savable) | 0.00% | – | – | – | – | – |
| R? (not savable) | 0.00% | – | – | – | – | – |
| U | 97.87% | – | – | – | – | – |

Tool-output tokens in items of 200+: 14,990; under 200: 602. N: 0 tokens; a reference plus its changed fields would cost 0. References 0, diffs 0, chunk pointers 1; repairs 0 (0 tokens); unsupported 0.00% of candidate tokens. Potentially stale current-state evidence: 0.00% of agent calls (a V-class source, only an older version visible, the call's action touches it); calls with any such stale copy visible: 0.00% (0 tokens). Broader context (any file whose visible read predates a later write to it, V class or not): 0.00% of calls; with the action touching it 0.00%.

### arm summary-32k: 3 runs, 17.3 PFLOPs

| class | share of tool-output tokens (200+) | share with a visible earlier copy | PFLOPs saved | share of total FLOPs | share of new-prefill FLOPs | concentration |
|---|---|---|---|---|---|---|
| E (references) | 0.00% | – | 0.000 | **0.00%** | 0.00% | – |
| V (diffs, beyond E) | 0.00% | – | 0.000 | 0.00% | 0.00% | – |
| P (chunk pointers, beyond E and V) | 11.42% (shared spans 4.05%) | 44.08% of shared spans | 0.081 | 0.47% | 0.95% | 2 of 3 runs give 80% |
| N (not savable) | 0.00% | – | – | – | – | – |
| R? (not savable) | 0.00% | – | – | – | – | – |
| U | 88.58% | – | – | – | – | – |

Tool-output tokens in items of 200+: 48,625; under 200: 2,905. N: 0 tokens; a reference plus its changed fields would cost 0. References 0, diffs 0, chunk pointers 118; repairs 0 (0 tokens); unsupported 0.00% of candidate tokens. Potentially stale current-state evidence: 0.00% of agent calls (a V-class source, only an older version visible, the call's action touches it); calls with any such stale copy visible: 0.00% (0 tokens). Broader context (any file whose visible read predates a later write to it, V class or not): 3.97% of calls; with the action touching it 0.79%.

### CLM's management cost (TBLite; an upper bound)

Calls: management-only 1.82%, mixed 0.21%, task-only 96.68%, no command 1.28%. Generation on management (management-only calls, and the management commands' own text in mixed calls): 1.39% of CLM's FLOPs. Edits: 33.

| edit type | edits | new text tokens | re-prefill: new summary text | reasoning made visible | new observations | retained text, reuse lost (avoidable) | lost-reuse PFLOPs | share of CLM's FLOPs |
|---|---|---|---|---|---|---|---|---|
| summarize | 19 | 99,416 | 58,596 | 38,282 | 11,782 | 59,472 | 3.190 | 3.14% |
| shorten | 14 | 55,899 | 17,694 | 36,936 | 6,528 | 34,963 | 1.847 | 1.82% |

Edits whose request could not be mapped message by message: 0 of 33.

## BrowseComp-Plus pilot

**(1) E savings, after repairs: 1.58% of total FLOPs. (2) Beyond E (V diffs + P chunks): 0.44%. (3) CLM's plausibly avoidable management cost (retained text whose cache reuse its edits lost): 23.47% of CLM's FLOPs (an upper bound).**


### Runs without edits (our harness: raw, raw-32k, base-32k, base): 4 runs, 6.4 PFLOPs

| class | share of tool-output tokens (200+) | share with a visible earlier copy | PFLOPs saved | share of total FLOPs | share of new-prefill FLOPs | concentration |
|---|---|---|---|---|---|---|
| E (references) | 15.36% | 93.81% | 0.737 | **11.57%** | 15.74% | 3 of 4 runs give 80% |
| V (diffs, beyond E) | 0.00% | – | 0.000 | 0.00% | 0.00% | – |
| P (chunk pointers, beyond E and V) | 58.42% (shared spans 11.66%) | 1.86% of shared spans | 0.010 | 0.16% | 0.22% | 1 of 4 runs give 80% |
| N (not savable) | 0.00% | – | – | – | – | – |
| R? (not savable) | 0.00% | – | – | – | – | – |
| U | 41.58% | – | – | – | – | – |

Tool-output tokens in items of 200+: 84,021; under 200: 80. N: 0 tokens; a reference plus its changed fields would cost 0. References 44, diffs 0, chunk pointers 9; repairs 0 (0 tokens); unsupported 0.00% of candidate tokens. Potentially stale current-state evidence: 0.00% of agent calls (a V-class source, only an older version visible, the call's action touches it); calls with any such stale copy visible: 0.00% (0 tokens). Broader context (any file whose visible read predates a later write to it, V class or not): 0.00% of calls; with the action touching it 0.00%.
E references here are snippet-level (repeated documents inside new search results). BrowseComp-Plus units (snippets and opened documents): E incidental 12,905 tokens, E deliberate 0, new 62,130.

### Runs with edits or management (econo+jev, summary, CLM): 8 runs, 107.4 PFLOPs

| class | share of tool-output tokens (200+) | share with a visible earlier copy | PFLOPs saved | share of total FLOPs | share of new-prefill FLOPs | concentration |
|---|---|---|---|---|---|---|
| E (references) | 6.38% | 24.85% | 1.064 | **0.99%** | 1.62% | 2 of 8 runs give 80% |
| V (diffs, beyond E) | 0.38% | 100.00% | 0.000 | 0.00% | 0.00% | – |
| P (chunk pointers, beyond E and V) | 59.35% (shared spans 14.87%) | 10.29% of shared spans | 0.495 | 0.46% | 0.76% | 4 of 8 runs give 80% |
| N (not savable) | 0.00% | – | – | – | – | – |
| R? (not savable) | 0.00% | – | – | – | – | – |
| U | 40.27% | – | – | – | – | – |

Tool-output tokens in items of 200+: 550,344; under 200: 6,872. N: 0 tokens; a reference plus its changed fields would cost 0. References 50, diffs 0, chunk pointers 259; repairs 0 (0 tokens); unsupported 10.67% of candidate tokens. Potentially stale current-state evidence: 0.00% of agent calls (a V-class source, only an older version visible, the call's action touches it); calls with any such stale copy visible: 0.00% (0 tokens). Broader context (any file whose visible read predates a later write to it, V class or not): 0.00% of calls; with the action touching it 0.00%.
E references here are snippet-level (repeated documents inside new search results). BrowseComp-Plus units (snippets and opened documents): E incidental 35,119 tokens, E deliberate 0, new 199,288.

### arm base: 4 runs, 6.4 PFLOPs

| class | share of tool-output tokens (200+) | share with a visible earlier copy | PFLOPs saved | share of total FLOPs | share of new-prefill FLOPs | concentration |
|---|---|---|---|---|---|---|
| E (references) | 15.36% | 93.81% | 0.737 | **11.57%** | 15.74% | 3 of 4 runs give 80% |
| V (diffs, beyond E) | 0.00% | – | 0.000 | 0.00% | 0.00% | – |
| P (chunk pointers, beyond E and V) | 58.42% (shared spans 11.66%) | 1.86% of shared spans | 0.010 | 0.16% | 0.22% | 1 of 4 runs give 80% |
| N (not savable) | 0.00% | – | – | – | – | – |
| R? (not savable) | 0.00% | – | – | – | – | – |
| U | 41.58% | – | – | – | – | – |

Tool-output tokens in items of 200+: 84,021; under 200: 80. N: 0 tokens; a reference plus its changed fields would cost 0. References 44, diffs 0, chunk pointers 9; repairs 0 (0 tokens); unsupported 0.00% of candidate tokens. Potentially stale current-state evidence: 0.00% of agent calls (a V-class source, only an older version visible, the call's action touches it); calls with any such stale copy visible: 0.00% (0 tokens). Broader context (any file whose visible read predates a later write to it, V class or not): 0.00% of calls; with the action touching it 0.00%.
E references here are snippet-level (repeated documents inside new search results). BrowseComp-Plus units (snippets and opened documents): E incidental 12,905 tokens, E deliberate 0, new 62,130.

### arm clm: 8 runs, 107.4 PFLOPs

| class | share of tool-output tokens (200+) | share with a visible earlier copy | PFLOPs saved | share of total FLOPs | share of new-prefill FLOPs | concentration |
|---|---|---|---|---|---|---|
| E (references) | 6.38% | 24.85% | 1.064 | **0.99%** | 1.62% | 2 of 8 runs give 80% |
| V (diffs, beyond E) | 0.38% | 100.00% | 0.000 | 0.00% | 0.00% | – |
| P (chunk pointers, beyond E and V) | 59.35% (shared spans 14.87%) | 10.29% of shared spans | 0.495 | 0.46% | 0.76% | 4 of 8 runs give 80% |
| N (not savable) | 0.00% | – | – | – | – | – |
| R? (not savable) | 0.00% | – | – | – | – | – |
| U | 40.27% | – | – | – | – | – |

Tool-output tokens in items of 200+: 550,344; under 200: 6,872. N: 0 tokens; a reference plus its changed fields would cost 0. References 50, diffs 0, chunk pointers 259; repairs 0 (0 tokens); unsupported 10.67% of candidate tokens. Potentially stale current-state evidence: 0.00% of agent calls (a V-class source, only an older version visible, the call's action touches it); calls with any such stale copy visible: 0.00% (0 tokens). Broader context (any file whose visible read predates a later write to it, V class or not): 0.00% of calls; with the action touching it 0.00%.
E references here are snippet-level (repeated documents inside new search results). BrowseComp-Plus units (snippets and opened documents): E incidental 35,119 tokens, E deliberate 0, new 199,288.

### CLM's management cost (BrowseComp-Plus pilot; an upper bound)

Calls: management-only 17.35%, mixed 1.56%, task-only 66.67%, no command 14.42%. Generation on management (management-only calls, and the management commands' own text in mixed calls): 3.22% of CLM's FLOPs. Edits: 87.

| edit type | edits | new text tokens | re-prefill: new summary text | reasoning made visible | new observations | retained text, reuse lost (avoidable) | lost-reuse PFLOPs | share of CLM's FLOPs |
|---|---|---|---|---|---|---|---|---|
| summarize | 51 | 200,755 | 100,237 | 79,270 | 10,653 | 49,588 | 2.552 | 2.38% |
| shorten | 6 | 19,600 | 8,508 | 10,564 | 1,215 | 12,263 | 0.635 | 0.59% |
| rollback (budget control) | 30 | 10,321 | 8,919 | 916 | 615 | 421,325 | 22.033 | 20.51% |

Edits whose request could not be mapped message by message: 0 of 87.
