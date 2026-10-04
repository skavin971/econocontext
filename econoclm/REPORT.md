# EconoCLM v1: Raw CLM vs EconoCLM on 10 TBLite tasks (Gemini 3.6 Flash, Vertex)

Status (2026-10-03): **Gate 2 PASS** (`runs/2026-10-03-g2-smoke`), **Gate 3 PASS** at
`max_tokens 8192` (`runs/2026-10-03-g3-pilot-raw-8192`; the 2048 pilot failed on length,
deviation 5), **Gate 4 done** (`runs/2026-10-03-main`: Raw CLM 8/10 passed, $1.82; 5 of 10 tasks
made 0 context edits, under the stop rule of 6; a first attempt was voided by a network
outage, `runs/2026-10-03-main-attempt1-network`). Gates 5–6 not run yet.
Sections 2–7 are filled in at Gate 6.

## 1. Setup

| Item | Value |
|---|---|
| Model | `google/gemini-3.6-flash` on Vertex's OpenAI-compatible endpoint (global); litellm id `openai/google/gemini-3.6-flash`. Exact served version: from Gate 2 (`reply model`) |
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
   - **Checked on Gate 4's 27 rewrites** (`analysis/rewrite_positions.py`, exact positions via `countTokens`): the live first-change estimate was off by a median 26 tokens, and R was exceeded once, by 26 tokens. A tighter `R_likely = min(c, A) − p` is recorded but not shown: it was exceeded 12 times, because CLM's tokenizer undercounts the rebuilt text.
   - **`quote_check.py`** splits each edit's actual re-read into *edit-caused* and *background* (Gemini's own misses), reports bound violations, and compares R with the edit-caused re-read only where the cache was healthy on both neighbouring calls.
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

### Other implementation notes

- **CLM flattens tool turns on every applied edit.** `parse_back` turns `tool_calls` into text and tool results into user turns. So an edit rewrites the request from the first turn added since the previous edit, even if the model edited later. The quote and status line price this, and `edit_ceiling.py` splits the re-read into *format change* and *edit position*.
- **Phases.** Each phase has its own runs folder and gateway ledger. The spend cap ($40) counts all ledgers together.
- **Gate 5 byte check.** `econo get N | sha1sum` runs in the sandbox at the end of each EconoCLM run, after the model's `econo` log is collected (table `get_checks`).

## 2. Results (Raw vs EconoCLM)

_Paste `runs/<D>-main/results.md` (summary and per-run tables)._

## 3. Edit ceiling

_Paste `runs/<D>-main/edit_ceiling.md`: per arm, the share of the bill re-read after rewrites, split into format change vs edit position, plus the append-only background._

**Background vs edit ceiling (Gate 4, Raw; `runs/2026-10-03-main/background.md`).** The background is the re-read on calls that do *not* follow a rewrite: per call, `max(0, uncached − newly appended tokens incl. their hidden thinking)`, priced at input − cached ($0.675/M). It is Gemini's own cache misses: a *tail* (the newest text the cache has not caught up with, at most the previous call's appended tokens) and *deeper* misses of older prefix. On Gate 4 Raw it was **$0.50 = 27.2% of the bill, twice the edit ceiling ($0.24 = 13.2%)**: tail $0.10, deeper misses $0.39; 59 of 200 append-only calls had no cache hit at all. Per task it ranged from 13% to 61% of the run's cost. No edit policy can remove it.

## 4. Quote accuracy

_Median absolute error of predicted R vs the actual extra uncached tokens on the next call, in tokens and as % of R (`quote_check.md`)._

## 5. Behavior

- `econo` usage (get, search, sql); fetches of cut outputs
- stale flags, and whether the model re-read those files
- repeated commands; rollbacks; edit counts and where edits happened

## 6. Anomalies

_Usage anomalies by arm ("Usage anomalies (ledger)" in `results.md`; must be 0, else list the rows and their usage), hook errors, failed or `infra_fail` trials, rate-limit waits by arm, anything surprising._

- **Rebuild rejected by Vertex** ("Trials ended by rebuild rejected by Vertex" in `results.md`, per arm). CLM's plain-text rebuild after an edit can end the message list with an assistant turn; Vertex refuses such requests (HTTP 400, "Requests ending with a model turn are not supported"), CLM retries and the trial fails. CLM is kept as released, and the trial counts as a task failure. Gate 4 Raw: 1 trial (`scan-linux-persistence-artifacts`: calls 31–35, four 400s and one gateway 502 while retrying the same request).

## 7. Plain reading and next step

- Differences of 1–2 passed tasks out of 10, or cost differences under about 20%, can be noise (Gemini's caching is partly random).
- Say which mechanisms visibly fired.
- Recommend one: a 2nd repeat, ablations, skill evolution, BrowseComp-Plus, or cache-aware commits (v2, only if the edit ceiling is about 10% of the bill or more).

## 8. Candidate later arms (noted, not built)

- **Format-preserving rebuild: dropped.** It would remove only the *format change* part of the edit ceiling, which was 0.4–0.5% of the bill at Gate 4 (Raw).
- **Cache-aware commits (v2): decide after Gate 6.** The edit ceiling was 13% of the Raw bill at Gate 4, above the ~10% bar, but concentrated in 2 tasks (`maven-slf4j-conflict` 43% of its run, `malicious-package-forensics` 15%); the other 8 were at or under 10%.
- **Cache-hit reliability: possible new arm.** The append-only background (section 3) is Gemini's own cache misses and was twice the edit ceiling at Gate 4. An arm that makes cache hits more reliable could save more than any edit policy. Nothing is built yet.
