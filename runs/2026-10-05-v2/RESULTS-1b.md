# Spike 1b: all nine optimizations, forced, in a Harbor container (2026-10-05)

Task `benchmarks/tblite/spike-all-nine`, Claude Code **2.1.290** (pinned), Sonnet 5, arm econo+jev, forced: 4, 5, 7, 8.

**Run 1** (`runs/v2spike`) reached no rule. Claude Code 2.1.290 blocks HTTP hooks to private addresses: "HTTP hook blocked: host.docker.internal resolves to 192.168.65.254 (private/link-local address)", 33 times. I switched to command hooks (curl), Claude Code's documented hook type, and pinned the version.

**Run 2** (`runs/v2spike2`): reward **1.0**, 16 calls, **$0.2202** run cost (transcript = gateway). Jev: 2 calls, 33,162 input tokens (about $0.0014, reported separately).

| # | Rule | Fired? | What the model saw | Agent afterwards | Finding |
|---|---|---|---|---|---|
| 1 | dont_repeat | **no** | — | — | The agent used Bash `grep` instead of the Grep tool. Rule 3 caught the identical read-only repeat first and served the **full** stored copy. **Rules 1 and 3 overlap**: when the earlier copy is still fully in context, the cheapest answer is the note, not the full copy |
| 2 | delta_read | **yes** (1) | Read 40–100 → lines 61–100 only, plus "Lines 40-60 are unchanged and already above…" | continued normally | works |
| 3 | serve_stored | **yes** (2 serve, 1 restore) | the repeated `grep` and `pytest` were served from the database, with "Nothing was written since you last ran this exact command…"; a repeated Read that Claude Code stubbed as `file_unchanged` was restored in full | continued normally | works; saves tool time, not tokens (see 1) |
| 4 | arrival | **yes** (2 slices, forced, Jev-chosen) | `cat data/big.log`: the hook saw only **470 lines (about 7,500 tokens)**, because Claude Code cuts Bash output before hooks. Our slice then sat inside Claude Code's own wrapper: "Output too large (157.9KB). Full output saved to …/tool-results/….txt. Preview (first 2KB)". | found SECRET_TOKEN with `grep` (cheap) | **Claude Code already offloads very large outputs natively** (a file + 2KB preview). Our rule adds value only for mid-size outputs (about 1k–7.5k tokens). The slice Jev picked (lines 361+) did not contain the token |
| 5 | worker_report | **no** | — | — | Claude Code 2.1.290 launches Agent calls **asynchronously** ("Async agent launched successfully … agentId"). The report arrives later as a message, not as the Agent tool's result, so PostToolUse on Agent never sees it |
| 6/7 | evict / compact | **no** | — | — | The forced compaction waits for 12 main-agent calls; the session ended first. **Not tested yet** |
| 8 | placement | **no** | — | — | The two workers ran in the background at the same time; no worker was idle when the second Agent call came |
| 9 | invalidate | **yes** (2) | `cat src/app.py` after the Edit was **not** served (epoch 0 → 1) and showed the edited file | wrote correct answers | works |

**Native Claude Code behaviour our baseline already includes** (so savings must be measured beyond it):
- identical re-reads of an unchanged file return a `file_unchanged` stub (spike B);
- outputs over about 30k characters are saved to a file with a 2KB preview (this run);
- workers run asynchronously.
