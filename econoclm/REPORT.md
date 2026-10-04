# EconoCLM v1: Raw CLM vs EconoCLM on 10 TBLite tasks (Gemini 3.6 Flash, Vertex)

Status (2026-10-03): **Gate 2 PASS** (`runs/2026-10-03-g2-smoke`), **Gate 3 PASS** at
`max_tokens 8192` (`runs/2026-10-03-g3-pilot-raw-8192`; the 2048 pilot failed on length,
deviation 5), **Gate 4 done** (`runs/2026-10-03-main`: Raw CLM 8/10 passed, $1.82; 5 of 10 tasks
made 0 context edits, under the stop rule of 6; a first attempt was voided by a network
outage, `runs/2026-10-03-main-attempt1-network`). **Gate 5 FAIL on one check** (`runs/2026-10-03-g5-pilot-econo`:
EconoCLM pilot, reward 1.0, $0.073; 1 call cut by length, the same call 1 and the same 8,188 output tokens as Raw on this
task at Gate 4; all other checks pass), **Gate 6 done** (`runs/2026-10-03-main`: EconoCLM 8/10 passed, $2.02; no
infra failures; shared parts unchanged). Sections 2–6 filled in from Gates 4 and 6; section 7 is left for review.


## 1. Setup

| Item | Value |
|---|---|
| Model | `google/gemini-3.6-flash` on Vertex's OpenAI-compatible endpoint (global); litellm id `openai/google/gemini-3.6-flash`. Exact served model: `google/gemini-3.6-flash` (the reply's `model` field in the Gate 2 rerun, 2026-10-03) |
| Prices (per 1M tokens) | $0.75 input, $0.075 cached input (90% implicit-cache discount), $3.75 output incl. thinking. From `config/billing_rates.yaml` on `feature/claude-code` (read 2026-09-27, promotional period until 2026-12-31) |
| Price mismatch noted | An older local `.env` (2026-09-21) listed `gemini-3.5-flash` at $1.50 / $0.15 / $9.00. Not used |
| Agent settings (both arms) | `context_budget_tokens 32000`, `max_tokens 8192` (the pre-set fallback; was 2048, see deviation 5), `max_steps 64`, `command_timeout 180`, `temperature 0.7`, `top_p 0.95`, `send_chat_template_kwargs false`, `cost_metric usd`; everything else at CLM defaults. Harbor `--agent-timeout-multiplier 4` |
| Only differences between arms | Agent class (`ClmAgent` vs `EconoClmAgent`), `skill_dirs`, and `econo_run_dir` (log location only). Checked by `tests/test_run_dry.py` |
| Commits | ours: `econoclm` @ _fill in_; frozen baselines: `frozen/econocontext-v0` = `2d62c2f`, `frozen/econocontext-v0-claude-code` = `ae9fd5a`; CLM `18dc111`; TBLite `5c37b41`; Harbor 0.16.1 |
| Tasks (frozen 2026-10-03) | seed 20261003 over the sorted TBLite list, then the oracle health check (`bench/tblite/health.md`; run twice with identical results, so no flaky tasks). The 10: `api-endpoint-permission-canonicalizer`, `sales-data-csv-analysis`, `acl-permissions-inheritance`, `maven-slf4j-conflict`, `chained-forensic-extraction_20260101_011957`, `pandas-etl`, `malicious-package-forensics`, `bandit-delayed-feedback`, `anomaly-detection-ranking`, `scan-linux-persistence-artifacts`. Changing the list means rerunning both arms |
| Swaps (7) | Placeholder reference solution (`solve.sh` only prints "no solution written"): `iris-dataset-classification` → `maven-slf4j-conflict`, `grid-pathfinding` → `html-index-analysis` → `malicious-package-forensics`, `prediction-model-evaluation` → `bandit-delayed-feedback`, `playing-card-recognition` (the replacement for `pdf-table-parsing`) → `pandas-etl`. Task image lacks a module its solution imports (`camelot`): `pdf-table-parsing` → `playing-card-recognition`. MLflow server on 127.0.0.1:5000 unreachable when the solution runs (same failure both times; cause not established, possibly specific to this arm64 host): `breast-cancer-mlflow` → `anomaly-detection-ranking` |
| Machine | MacBook Air (Mac14,15), Apple M2, 8 cores (4 performance + 4 efficiency), 16 GB RAM, macOS 26.6.2. Docker Desktop 28.3.2; its VM has 8 CPUs and 7.65 GiB RAM (kernel 6.10.14-linuxkit) |
| Task architecture | Harbor builds each task image for the Docker daemon's platform, here `linux/arm64`, and the containers run natively (`uname -m` = `aarch64`). No x86 emulation was used, and `DOCKER_DEFAULT_PLATFORM` was not set. A Linux x86_64 host would run the same tasks on amd64, so pass/fail on a task can differ between hosts; both arms here use the same host |
| Shared parts (frozen) | Tag `econoclm-shared-v1` = commit `8a95418`, pushed 2026-10-03 before Gate 4: `econoclm/core/gateway.py`, `econoclm/core/gateway_ledger.py`, `econoclm/core/usage.py`, `econoclm/core/prices.py`, `econoclm/core/meter.py`, `econoclm/core/types.py`, `econoclm/core/sqlite_util.py`, `econoclm/core/tokenizer.py`, `econoclm/bench/tblite/run.py`, `econoclm/bench/tblite/select_tasks.py`, `econoclm/bench/tblite/tasks.txt`, `econoclm/arms/raw_clm/config.yaml`. Gate 4 and Gate 6 both run on them; before Gate 6, `git diff --exit-code econoclm-shared-v1 -- <these files>` must print nothing. The gateway runs with `--log-bodies` at Gates 4–6 (request bodies and replies saved per call, never headers) |
| Parallelism (both arms) | `--workers 2` trials at a time, gateway `MAX_INFLIGHT=2`. Lower than the planned 4, to keep 2 containers plus the agents inside the 7.65 GiB Docker VM and avoid command timeouts from overload |
| `enable_thinking` | No effect here: CLM sends it only inside `chat_template_kwargs`, which is off. Gemini thinks by default; thinking is billed as output |

### Deviations from the prompt (approved, the same for both arms)

1. **Gateway retries of upstream 429/503.**
   - The gateway resends the same bytes, with backoff, for at most 240 s. That stays under CLM's 600 s call timeout, so CLM never resends a call.
   - Why: CLM gives up after 5 failed attempts and the trial would crash, which counts as a task failure caused by quota, not by the agent.
   - The ledger records `upstream_attempts`, `ratelimit_wait_ms` and `queue_ms` (at most 2 calls in flight, `MAX_INFLIGHT=2`). Latency is the final attempt only, and wall time is reported both raw and net of these waits.
   - A trial that still dies on rate limits is marked `infra_fail`.
2. **The bundled tokenizer file.**
   - CLM counts with tiktoken `o200k_base`. Its download was blocked in the cloud sandbox, and CLM then silently falls back to chars/4.
   - Every entry point therefore points `TIKTOKEN_CACHE_DIR` at the copy bundled with litellm (`core/tokenizer.py`). tiktoken hash-checks it, and it is used locally too, so both arms and both machines match.
3. **Token units, Gemini's hidden thinking, and the quote** (EconoCLM only; reworked 2026-10-03 after Gates 3–4, `quote/hidden.py`).
   - **Hidden part.** Every tool-call turn carries its call's thinking as an opaque `thought_signature`, and Gemini may bill that thinking as prompt tokens on later calls. CLM's own count never sees it: it was 59% of the Gate 3 pilot's prompt tokens.
   - **Three billing modes.** Gemini handles it in one of three ways per call: *none* (no earlier thinking counted), *live* (the thinking of tool-call turns since the last answered user message), or *all* (every signed turn, no reset). This was found on Gate 4 with the exact hidden part from Vertex `countTokens`: 48 / 100 / 60 of 208 calls, each within 170 tokens of one mode. Which mode applies varies by run and by call.
   - **Measured, not predicted.** After each call, `HiddenMeter` compares the billed prompt with `k` × CLM's count and snaps to the nearest mode. On Gate 4 it was off by a median 32 tokens, p90 128. The old single `k` was off by 303 / 3,948, and the fixed rule alone by 56 / 1,753.
   - **`k` recalibration.** `k` is Gemini tokens per CLM-tokenizer token of *visible* text. It is recalibrated whenever the hidden part is known: calls with no signed turn (a run's first call, calls after the rebuild that follows an edit) and calls whose mode is clear-cut.
   - **`countTokens` validation.** It is free and was run offline on every logged request (`analysis/count_tokens.py`). The billed prompt equals countTokens + 11 on every call with no hidden part, so the exact hidden part = billed − countTokens − 11.
   - **The quote.** It shows R as an *upper bound* (`up to ~R re-read`) with the recent cache-hit rate. The first change is placed at the first differing character, since CLM's rebuild often keeps a message's text and appends to it.
   - **Cache minimum.** Gemini 3.6 Flash's implicit cache needs at least 4,096 tokens (ai.google.dev caching docs, updated 2026-09-02). On Gate 4, none of the 18 rewrites whose unchanged prefix was below that got any cache hit afterwards; 7 of the 9 above it did. So R = c when the unchanged prefix is below 4,096, else c − p. The status line's edit depths use the same rule.
   - **Checked on Gate 4's 27 rewrites** (`analysis/rewrite_positions.py`, exact positions via `countTokens`): the live first-change estimate was off by a median 26 tokens, and R was exceeded in 0 of 27 rewrites (once, by 26 tokens, before the cache-minimum rule). A tighter `R_likely = min(c, A) − p` is recorded for analysis only, never shown: it is not a safe bound (exceeded 11 times, because CLM's tokenizer undercounts the rebuilt text).
   - **`quote_check.py`** splits each edit's actual re-read into *edit-caused* and *post-edit misses* (Gemini's own misses after a rewrite), reports bound violations, and compares R with the edit-caused re-read only where the cache was healthy on both neighbouring calls.
4. **Billed output when Vertex omits `completion_tokens`** (found at Gate 2, 2026-10-03).
   - Vertex leaves `completion_tokens` out when a reply has no visible text (thinking only, cut by `max_tokens`). The usage logic copied from the frozen `omnigent_layer/wire.py` (`feature/claude-code` @ `ae9fd5a`) then recorded output as empty and billed none of the thinking: the first smoke call was charged for its 6 prompt tokens only, not its 13 thinking tokens.
   - `core/usage.py` now bills output = `completion_tokens` + `reasoning_tokens` (a missing one counts as 0), and uses `total_tokens − prompt_tokens` when a component is missing and the total is there.
   - The ledger marks `usage_anomaly = 1` when prompt + completion + reasoning ≠ total, or a 200 reply has no usable output count. `gate_check` fails on any, and `results.md` counts them per arm (section 6).
   - The ledger also stores each call's usage JSON as received (`raw_usage`), so tokens and costs can be recomputed if the parsing changes again. Added after the Gate 3 pilot had started: the Gate 2 and Gate 3 pilot ledgers lack it; Gates 4–6 have it.
   - The frozen code is left untouched. Frozen `wire.py` likely has the same gap, so the v0 bills may undercount thinking on replies with no visible text.
   - Gate 2's smoke request now asks for `max_tokens 256` (was 16) and passes only with `finish_reason = stop`, non-empty visible text, and a cost that includes the output tokens.
   - Both arms kept `max_tokens 2048` for parity with CLM until Gate 3 (deviation 5).
5. **`max_tokens` 8192 in both arms** (the pre-set fallback, 2026-10-03). The Gate 3 raw pilot at 2048 had 2 of 22 calls cut by length, and most calls spent about 1,960 thinking tokens. Pilot reruns in `runs/2026-10-03-g3-pilot-raw-8192`.
6. **Provider-side retries.** When Gemini answers `finish_reason = malformed_function_call`, the call is resent with the same prompt below CLM's step counter, so the gateway sees one more call than CLM counts. `gate_check` allows one extra gateway row per such reply (any other extra row fails), and `results.md` counts them per arm ("Provider-side retries"). They are billed and included in cost.

7. **Length cuts reported, not gated** (pre-set before Gate 6, 2026-10-03).
   - At Gate 5 the EconoCLM pilot had one call cut by length. It was call 1 of `api-endpoint-permission-canonicalizer`, with 8,188 output tokens, 7,861 of them thinking: the same call and the same counts as Raw on that task at Gate 4.
   - The one pre-set fallback (`max_tokens 8192`, both arms) is already in use. So length cuts are treated as a property of the task: Gate 6 reports them per arm and per task (`results.md`, "Calls cut by length") and does not fail on them. `max_tokens` stays 8192 in both arms, so Gate 4 stands.

8. **Tool-engagement arms v1.1 and v1.2: pre-set rule** (written 2026-10-03, before either arm ran).
   - **The arms.**
     - v1.1 "facts only" (`arms/econo_clm_v11/`):
       - An EconoCLM section in this arm's system prompt (its own `SKILL.md`, which CLM appends; CLM's root prompt unchanged), including the fact that Gemini caches only prompts of at least 4,096 tokens and only up to the first changed character.
       - Cut-output tags that print the exact command for the missing lines, e.g. `[obs 3] lines 120-310 not shown: econo get 3 120-310`, computed from CLM's head/tail cut. If CLM cut the output again to fit the budget, the v1 wording is kept.
       - `econo note` / `econo notes`, logged as writes and reads.
       - Everything else as in v1: status line, quote, stale flags.
     - v1.2 "guided" (`arms/econo_clm_v12/`): v1.1 plus three sentences on how to use the tools. The fourth draft sentence, about edits near the start, was dropped because CLM's own prompt already says that.
     - The two arms share one agent class (`arms/econo_clm_v11/agent.py`) and one `econo` tool, and differ only in `SKILL.md`. They run through `bench/tblite/run_arms.py`, which registers the arms `econo11` and `econo12` at runtime and leaves the frozen `run.py` unchanged.
     - Shared parts stay frozen at `econoclm-shared-v1`. Gate 4 Raw stays the baseline.
   - **Engagement rule.** An arm *engages* if the model uses `econo` (get, search, note or sql) in at least 3 of the 10 runs.
   - **Runs.** The same 10 tasks and settings as Gate 6: `--workers 2`, body logging on, and the infra-rerun rule.

### Other implementation notes

- **CLM flattens tool turns on every applied edit.** `parse_back` turns `tool_calls` into text and tool results into user turns. So an edit rewrites the request from the first turn added since the previous edit, even if the model edited later. The quote and status line price this, and `edit_ceiling.py` splits the re-read into *format change* and *edit position*.
- **Phases.** Each phase has its own runs folder and gateway ledger. The spend cap ($40) counts all ledgers together.
- **Gate 5 byte check.** `econo get N | sha1sum` runs in the sandbox at the end of each EconoCLM run, after the model's `econo` log is collected (table `get_checks`).

## 2. Results (Raw vs EconoCLM)

One rep per arm per task. Raw ran on 2026-10-03 (Gate 4) and EconoCLM ran later the same evening (Gate 6). Both used the shared parts at `econoclm-shared-v1`, `--workers 2` and `max_tokens 8192`. Full tables: `runs/2026-10-03-main/results.md` and `results.csv`.

| Metric | Raw | EconoCLM |
|---|---|---|
| Tasks passed | 8 / 10 | 8 / 10 |
| Total $ | $1.820 | $2.023 (+11%) |
| $ per solved task | $0.228 | $0.253 |
| Input tokens (cached / uncached) | 3.01M (1.61M / 1.41M) | 3.09M (1.65M / 1.43M) |
| Output + thinking tokens | 171.8K | 219.9K |
| Model calls | 240 | 225 |
| Context edits / rollbacks | 24 / 6 | 22 / 5 |
| Calls cut by length (deviation 7) | 2 | 2 |
| Mean peak context (CLM count) | 12.8K | 15.0K |
| Mean wall time per task | 139 s | 167 s |
| Infra failures; rate-limit retries (wait) | 0; 1 (4.7 s) | 0; 1 (6.9 s) |

**Per task**

| Task | Raw pass | Econo pass | Raw $ | Econo $ | Raw calls | Econo calls | Raw edits | Econo edits |
|---|---|---|---|---|---|---|---|---|
| acl-permissions-inheritance | 1 | 1 | 0.045 | 0.010 | 13 | 4 | 0 | 0 |
| anomaly-detection-ranking | 1 | 1 | 0.066 | 0.108 | 14 | 17 | 0 | 0 |
| api-endpoint-permission-canonicalizer | 1 | 1 | 0.100 | 0.388 | 8 | 27 | 0 | 2 |
| bandit-delayed-feedback | 0 | 0 | 0.210 | 0.416 | 35 | 40 | 2 | 4 |
| chained-forensic-extraction | 1 | **0** | 0.077 | 0.045 | 14 | 7 | 0 | 0 |
| malicious-package-forensics | 1 | 1 | 0.634 | 0.147 | 73 | 22 | 14 | 2 |
| maven-slf4j-conflict | 1 | 1 | 0.228 | 0.165 | 28 | 23 | 4 | 7 |
| pandas-etl | 1 | 1 | 0.080 | 0.029 | 11 | 9 | 1 | 0 |
| sales-data-csv-analysis | 1 | 1 | 0.085 | 0.070 | 13 | 12 | 0 | 0 |
| scan-linux-persistence-artifacts | **0** | 1 | 0.293 | 0.646 | 31 | 64 | 3 | 7 |

- **Same pass count, different tasks.** Raw failed `scan-linux` (its rebuild was rejected by Vertex, §6); EconoCLM failed `chained-forensic` (reward 0, no exception). Both failed `bandit`.
- **Tasks solved by both (7).** EconoCLM was cheaper on 5 of 7: −$0.32 in total, median −$0.04 per task.
- **Where the extra cost came from.** The arm total is higher because of `api-endpoint` (27 calls vs 8, two length cuts), `bandit`, and `scan-linux`, which EconoCLM finished and Raw did not.
- With one rep per task these differences are within the noise band set in §7.

## 3. Edit ceiling

Shares of each arm's bill, priced at input − cached per re-read token (`runs/2026-10-03-main/edit_ceiling.md`). The edit-caused split uses exact positions for all 53 rewrites (`rewrite_positions.md`).

| | Raw | EconoCLM |
|---|---|---|
| Rewrites (edits + rollbacks) | 27 | 26 |
| **Edit-caused (the edit ceiling)** | **7.1%** ($0.129) | **4.0%** ($0.080) |
| of which: lost prefix (unchanged prefix < 4,096) | 1.5% | 1.2% |
| of which: format change | 0.4% | 0.2% |
| of which: edit position | 5.3% | 2.6% |
| Post-edit misses | 6.1% | 4.3% |
| **Append-only misses** | **27.2%** | **25.7%** |
| Rewrites whose unchanged prefix was below the cache minimum | 18 of 27 | 17 of 26 |

Two kinds of re-read are not caused by edits. Both are Gemini's own cache misses, priced at input − cached ($0.675/M):

- **Append-only misses**: on calls that do *not* follow a rewrite, `max(0, uncached − newly appended tokens incl. their hidden thinking)`. This is a *tail* (the newest text the cache has not caught up with, at most the previous call's appended tokens) plus *deeper* misses of older prefix (`runs/2026-10-03-main/append_only_misses.md`).
- **Post-edit misses**: on calls that follow a rewrite, the part of the re-read the edit did not cause (`analysis/rewrite_positions.py`).

**Gate 4 (Raw).**
- Append-only misses: **$0.50 = 27.2% of the bill** (tail $0.10, deeper $0.39). 59 of 200 append-only calls had no cache hit at all; per task 13% to 61% of the run's cost.
- Edit ceiling (edit-caused): **7.1%**. Of that, 1.5% is lost prefix, 0.4% format change and 5.3% edit position.
- Post-edit misses: **6.1%**.
- The earlier 13.2% "edit ceiling" counted all re-read after a rewrite as edit-caused.
- **Cache minimum.** Gemini's implicit cache needs a 4,096-token prefix. When an edit leaves a shorter unchanged prefix, the cache serves none of it. That prefix (39,628 tokens on Gate 4, 1.5% of the bill) therefore moves from post-edit misses to edit-caused ("lost prefix").

Edit policies can't remove Gemini's misses on append-only calls; a runtime caching arm might (§8). At Gate 6 they were again the largest re-read in both arms (27.2% and 25.7%), 4–6× the edit-caused ceiling.

## 4. Quote accuracy

EconoCLM's quotes, checked at Gate 6 (`runs/2026-10-03-main/quote_check.md`, exact positions from `rewrite_positions.json`):

- **Volume:** 22 edits quoted; 21 compared with the next call, all with exact positions.
- **Bound violations:** 0 (edit-caused re-read > R). Across all 53 rewrites in both arms: also 0.
- **Re-read after the 21 edits:** 98,596 tokens edit-caused, 41,178 post-edit misses.
- **Healthy-cache edits (10, both neighbouring calls hit):** median |R − edit-caused| = 7,241 tokens, 74% of R.
  - R is a loose upper bound, as designed. The analysis-only `R_likely` was off by a median 362 tokens there, but it was exceeded in 24 of 53 rewrites, so it is still not shown.
- **First-change position:** the live estimate p was off from the exact P by a median 34 tokens over the 53 rewrites.
- **Hidden-thinking accounting (deviation 3):** validated offline on Gate 4. On every call with no hidden part, the billed prompt = countTokens + 11.

## 5. Behavior

- **`econo` use: none.** The model ran no `econo` command in any of the 10 EconoCLM runs (no get, search or sql; DB reads and writes 0).
- **Cut outputs:** CLM cut 19 outputs in context. The tag said `(cut in context; full: econo get <id>)`; 0 were fetched back.
- **Stale flags:** 3 shown; none was followed by a re-read of that file.
- **What EconoCLM added:** on average 95 tokens of `[econo]` text per turn (tags plus status line).
- **Edits:** 22 in EconoCLM (24 in Raw), in 5 tasks: `scan-linux` 7, `maven` 7, `bandit` 4, `api-endpoint` 2, `malicious` 2.
  - Most edits removed long runs of earlier outputs (for example `bandit` turn 19 removed obs 6–18).
  - In `maven`, 5 of the 7 edits removed no tagged output.
- **Where edits happened** (exact first change P):
  - EconoCLM: median 3,904 tokens, 45% of the context the edit rewrote. Raw: median 3,463 tokens, 34%.
  - In both arms about two thirds of rewrites changed something inside the first 4,096 tokens (EconoCLM 17 of 26, Raw 18 of 27), so the cache served nothing afterwards.
  - Nothing in the data shows that the quotes moved edits deeper. The model never referred to them.
- **Rollbacks:** 5 (Raw 6).
- **Repeated identical commands:** 23 (Raw 17).

## 6. Anomalies

_Usage anomalies by arm ("Usage anomalies (ledger)" in `results.md`; must be 0, else list the rows and their usage), hook errors, failed or `infra_fail` trials, rate-limit waits by arm, anything surprising._

- **Gate 6 summary (both arms).**
  - Usage anomalies 0 / 0. Hook errors (EconoCLM) 0. Infra failures 0 / 0.
  - Provider-side retries (`malformed_function_call`) 3 / 3. Rate-limit retries 1 / 1.
  - Calls cut by length 2 / 2: Raw in `api-endpoint` and `scan-linux`; EconoCLM twice in `api-endpoint` (deviation 7).
  - Trials ended by rebuild rejected by Vertex: 1 / 0.
- **Failed trials.**
  - Raw: `bandit` (reward 0), `scan-linux` (rebuild rejected).
  - EconoCLM: `bandit` and `chained-forensic` (reward 0, no exception).
- **Network outage at Gate 4.** It voided the first Raw attempt (`runs/2026-10-03-main-attempt1-network`), which was rerun in full.
- **Unexplained cache behaviour.** `malicious-package-forensics` call 16 (Gate 4) was served 16,289 cached tokens although the request differed from the previous one after about 10,108. The cause is not found; Gemini's implicit cache may not be strictly prefix-based.
- **Rebuild rejected by Vertex** ("Trials ended by rebuild rejected by Vertex" in `results.md`, per arm). CLM's plain-text rebuild after an edit can end the message list with an assistant turn; Vertex refuses such requests (HTTP 400, "Requests ending with a model turn are not supported"), CLM retries and the trial fails. CLM is kept as released, and the trial counts as a task failure. Gate 4 Raw: 1 trial (`scan-linux-persistence-artifacts`: calls 31–35, four 400s and one gateway 502 while retrying the same request).

## 7. Plain reading and next step

_Draft for review._

- **Accuracy:** 8/10 in both arms, with different failures. No sign of harm or help.
- **Cost:** +11% overall, inside the ~20% noise band. Per-task swings of up to 4× show that run-to-run variation dominates at one run per task.
- **Mechanisms:** none fired. `econo` was never used, cut outputs were never fetched, stale flags were never followed, and the quotes did not visibly change where edits happened.
- **Main cost driver in both arms:** Gemini's own cache misses (26–27% of the bill), which v1 does not address.
- **Next:** tool-engagement arms (v1.1 facts, v1.2 guided; deviation 8). Then either the runtime arm for the 4,096-token cache minimum, or BrowseComp-Plus once an arm engages.

_Standing guidance: differences of 1–2 passed tasks out of 10, or cost differences under about 20%, can be noise (Gemini's caching is partly random)._

## 8. Candidate later arms (noted, not built)

- **Format-preserving rebuild: dropped.** It would remove only the *format change* part of the edit ceiling, which was 0.4–0.5% of the bill at Gate 4 (Raw).
- **Cache-aware commits (v2): decide after Gate 6.**
  - At Gate 4 (Raw) the edit-caused ceiling was 7.1% of the bill once post-edit misses were split out: below the ~10% bar.
  - The earlier 13% included post-edit misses, and most of the re-read was in 2 tasks (`maven-slf4j-conflict`, `malicious-package-forensics`).
- **Keep the unchanging prefix above the cache minimum: possible new arm.**
  - Gemini caches nothing when an edit leaves fewer than 4,096 unchanged tokens. On Gate 4, 18 of 27 rewrites did, and lost their whole cache (lost prefix 1.5% of the bill).
  - An arm could keep a fixed, never-edited prefix of at least 4,096 tokens (system + task + pinned context), so every edit keeps that prefix cached. Not built.
- **Fair control for the guided arm (only if v1.2 shows a gain): "Raw CLM + the same advice using plain files".**
  - For example, save outputs to `/tmp/notes` before deleting them from the context.
  - It would separate the value of the database (`econo`) from the value of the advice. Not built.
- **Cache-hit reliability: possible new arm.**
  - Append-only misses (§3) are Gemini's own cache misses and were 27.2% of the Gate 4 bill, almost 4× the edit-caused ceiling.
  - A runtime caching arm (for example explicit context caching of the stable prefix) could save more than any edit policy. Not built.
