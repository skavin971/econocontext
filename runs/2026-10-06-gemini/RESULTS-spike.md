# Our own Gemini agent: smoke and forced spikes (2026-10-06)

- **Smoke** (`gsmoke`, raw, sales-data-csv-analysis): the harness works end to end; the task **failed** on a data-cleaning choice (the grader expected Region B mean 71.28, the agent got 90.32). 21 calls, **$0.263** (31.8k output tokens, about half the input cached). Prefix 1,502 tokens; prompts grew to 39k; Gemini cached nothing under about 4k tokens and missed the cache entirely on 5 of the 16 later calls.
- **Spike 1** (`gspike`, rules 4, 6, 7 forced): reward 1.0, $0.052. Rules 1, 2, 3, 4, 7 and 9 fired. **Jev was never reached**: the key was not passed to Harbor's process, so every answer was the fixed guess. Fixed in `run_gemini.py`.
- **Spike 2** (`gspike2`, rules 6 and 7 forced; arrival left to Jev): reward 1.0, $0.052, Jev 2 calls.

| # | Rule | Spike 2 | Evidence |
|---|---|---|---|
| 1 | dont_repeat | **works** | the identical `grep` got the note |
| 2 | delta_read | **works** | read 40-100 trimmed to the new lines |
| 3 | serve_stored | **works** | after the compaction, the repeated `grep` was served (copy gone); in spike 1 the tiny `pytest` repeat was served because a note would cost more than its 25 tokens |
| 4 | arrival | **works**, with a Jev limit | `seq -w 1 1200`: Jev chose a slice (needed_again 0.1, lifetime this_call). `cat data/big.log` (about 40k tokens): **Jev HTTP 400, max_tokens_exceeded**, so the fixed guess was used |
| 6 | evict | **not exercised** | nothing big stayed full: Jev shrank the mid-size output on arrival |
| 7 | compact | **works** (forced) | the model's own summary: 272 tokens kept, 1,220 output tokens for the call; 16 messages removed. Jev's segment_done was 0.55 (unforced, it would not have compacted); logged price keep 8,366 vs compact 8,524 |
| 9 | invalidate | **works** | each write_file bumped the epoch |

**Jev's input limit** (probe): about 30,290 input tokens accepted, about 50k refused (`max_tokens_exceeded`). Every Jev request is now cut to 28,000 tokens: the conversation from the front first, then a long tool result to its head and tail. Lines cut from a tool result cannot be chosen for a slice.
