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

## Run 3 (`runs/v2spike3`): plain Claude Code workers, repeat priced among note / serve / run

Reward **1.0**, 23 calls, **$0.4851** (transcript = gateway). Jev: 4 calls, 52,542 input tokens (about $0.0022, separate). Forced: 4, 5, 8. Compaction left to fire naturally (user's decision); it did not, because the conversation never reached the 20k-token minimum.

| # | Rule | Run 3 | Evidence |
|---|---|---|---|
| 1 | dont_repeat | **works** | the second identical `grep` got "Same output as your earlier identical call (item 1); nothing was written since, so it is unchanged and still above." |
| 2 | delta_read | **works** (2) | |
| 3 | serve_stored | **works** (1 serve, 1 restore) | the repeated `pytest` was served; a `file_unchanged` re-read of a sliced file was restored in full |
| 4 | arrival | **works** (3 slices, forced, Jev-chosen) | |
| 5 | worker_report | **works** (1 slice, forced) | with plain workers, the report arrives as the Agent tool's result |
| 6/7 | evict / compact | not exercised | below the 20k-token conversation minimum; to be seen in the long tasks |
| 8 | placement | **not exercised** | the agent issued both Agent calls in one message, so neither worker was idle when the second started; both became idle afterwards (session state). The mechanism (deny → SendMessage) is still untested live |
| 9 | invalidate | **works** (2) | |

## Experiment pair 1: maven-slf4j-conflict (`runs/v2x`)

| | baseline | econo+jev |
|---|---|---|
| reward | 1.0 | 1.0 |
| calls | 21 | 25 |
| run cost (transcript) | $0.5937 | $0.5653 (−4.8%, within noise) |
| Jev (separate) | — | 8 calls, 132,317 input tokens, about $0.0056 |
| rules | — | arrival: 6 slice, 1 full; invalidate ×18; compaction **decided** once (call 14: Jev portion_done 0.69, phase exploring; keep 53,049 vs compact 21,449 units, H* 5.8) |

**The compaction was not carried out (a bug in our driver):** the driver asked for the flag with `?run=v2x:econo+jev:…` unquoted. In a query string `+` reads as a space, so the lookup never matched and the flag stayed pending (`econo_compactions: 0`). Fixed by quoting the run name, with a test. **This econo run measured arrival slices only.**

## Experiment pair 1, rerun: maven-slf4j-conflict with compaction working (`runs/v2x2`)

**Cost measurement corrected:** run cost = gateway calls + transcript calls the gateway never logged. The transcript omits Claude Code's `/compact` summary call; the gateway omits cut-off streams.

| | baseline (v2x) | econo+jev run 1 (v2x, compaction blocked by bug) | econo+jev run 2 (v2x2) |
|---|---|---|---|
| reward | 1.0 | 1.0 | 1.0 |
| run cost | $0.5936 | $0.5653 (−5%) | **$0.7283 (+23%)** |
| rules | — | 6 slice, 1 full, compaction decided but not run | 3 slice, 2 preview; **1 compaction run** (call 13; 1 item dropped) |
| Jev (separate) | — | about $0.0056 | about $0.0030 |

**Finding:** Claude Code's `/compact` call alone cost **$0.2019**: 16,783 output tokens (summary + thinking), and the summary kept was 10,630 tokens, against the 1,500-token placeholder in the price. At the true cost the compaction lost money; the rest of the session cost $0.526 (−11% vs baseline). The compaction price needs a real summary cost, not a placeholder.
