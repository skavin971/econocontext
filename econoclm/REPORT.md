# EconoCLM v1: Raw CLM vs EconoCLM on 10 TBLite tasks (Gemini 3.6 Flash, Vertex)

Status: **code complete; Gates 2–6 not run yet** (they run locally, see RUN_LOCAL.md).
Sections 2–7 are filled in at Gate 6.

## 1. Setup

| Item | Value |
|---|---|
| Model | `google/gemini-3.6-flash` on Vertex's OpenAI-compatible endpoint (global); litellm id `openai/google/gemini-3.6-flash`. Exact served version: from Gate 2 (`reply model`) |
| Prices (per 1M tokens) | $0.75 input, $0.075 cached input (90% implicit-cache discount), $3.75 output incl. thinking. From `config/billing_rates.yaml` on `feature/claude-code` (read 2026-09-27, promotional period until 2026-12-31) |
| Price mismatch noted | An older local `.env` (2026-09-21) listed `gemini-3.5-flash` at $1.50 / $0.15 / $9.00. Not used |
| Agent settings (both arms) | `context_budget_tokens 32000`, `max_tokens 2048`, `max_steps 64`, `command_timeout 180`, `temperature 0.7`, `top_p 0.95`, `send_chat_template_kwargs false`, `cost_metric usd`; everything else at CLM defaults. Harbor `--agent-timeout-multiplier 4` |
| Only differences between arms | Agent class (`ClmAgent` vs `EconoClmAgent`), `skill_dirs`, and `econo_run_dir` (log location only). Checked by `tests/test_run_dry.py` |
| Commits | ours: `econoclm` @ _fill in_; frozen baselines: `frozen/econocontext-v0` = `2d62c2f`, `frozen/econocontext-v0-claude-code` = `ae9fd5a`; CLM `18dc111`; TBLite `5c37b41`; Harbor 0.16.1 |
| Tasks (frozen 2026-10-03) | seed 20261003 over the sorted TBLite list, then the oracle health check (`bench/tblite/health.md`; run twice with identical results, so no flaky tasks). The 10: `api-endpoint-permission-canonicalizer`, `sales-data-csv-analysis`, `acl-permissions-inheritance`, `maven-slf4j-conflict`, `chained-forensic-extraction_20260101_011957`, `pandas-etl`, `malicious-package-forensics`, `bandit-delayed-feedback`, `anomaly-detection-ranking`, `scan-linux-persistence-artifacts`. Changing the list means rerunning both arms |
| Swaps (7) | Placeholder reference solution (`solve.sh` only prints "no solution written"): `iris-dataset-classification` → `maven-slf4j-conflict`, `grid-pathfinding` → `html-index-analysis` → `malicious-package-forensics`, `prediction-model-evaluation` → `bandit-delayed-feedback`, `playing-card-recognition` (the replacement for `pdf-table-parsing`) → `pandas-etl`. Task image lacks a module its solution imports (`camelot`): `pdf-table-parsing` → `playing-card-recognition`. MLflow server on 127.0.0.1:5000 unreachable when the solution runs (same failure both times; cause not established, possibly specific to this arm64 host): `breast-cancer-mlflow` → `anomaly-detection-ranking` |
| Machine | MacBook Air (Mac14,15), Apple M2, 8 cores (4 performance + 4 efficiency), 16 GB RAM, macOS 26.6.2. Docker Desktop 28.3.2; its VM has 8 CPUs and 7.65 GiB RAM (kernel 6.10.14-linuxkit) |
| Task architecture | Harbor builds each task image for the Docker daemon's platform, here `linux/arm64`, and the containers run natively (`uname -m` = `aarch64`). No x86 emulation was used, and `DOCKER_DEFAULT_PLATFORM` was not set. A Linux x86_64 host would run the same tasks on amd64, so pass/fail on a task can differ between hosts; both arms here use the same host |
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
3. **Token-unit conversion `k`.**
   - Gemini reports cached tokens in its own tokenizer's units; our positions use CLM's tokenizer.
   - The quote and status line scale our counts by `k` = the last call's reported prompt tokens ÷ our count of the same messages.
   - `quote_check.py` measures the resulting error.
4. **Billed output when Vertex omits `completion_tokens`** (found at Gate 2, 2026-10-03).
   - Vertex leaves `completion_tokens` out when a reply has no visible text (thinking only, cut by `max_tokens`). The usage logic copied from the frozen `omnigent_layer/wire.py` (`feature/claude-code` @ `ae9fd5a`) then recorded output as empty and billed none of the thinking: the first smoke call was charged for its 6 prompt tokens only, not its 13 thinking tokens.
   - `core/usage.py` now bills output = `completion_tokens` + `reasoning_tokens` (a missing one counts as 0), and uses `total_tokens − prompt_tokens` when a component is missing and the total is there.
   - The ledger marks `usage_anomaly = 1` when prompt + completion + reasoning ≠ total, or a 200 reply has no usable output count. `gate_check` fails on any, and `results.md` counts them per arm (section 6).
   - The ledger also stores each call's usage JSON as received (`raw_usage`), so tokens and costs can be recomputed if the parsing changes again. Added after the Gate 3 pilot had started: the Gate 2 and Gate 3 pilot ledgers lack it; Gates 4–6 have it.
   - The frozen code is left untouched. Frozen `wire.py` likely has the same gap, so the v0 bills may undercount thinking on replies with no visible text.
   - Gate 2's smoke request now asks for `max_tokens 256` (was 16) and passes only with `finish_reason = stop`, non-empty visible text, and a cost that includes the output tokens.
   - Both arms keep `max_tokens 2048` for parity with CLM. Thinking counts against it, so Gate 3's length check decides the 8192 fallback (both arms).

### Other implementation notes

- **CLM flattens tool turns on every applied edit.** `parse_back` turns `tool_calls` into text and tool results into user turns. So an edit rewrites the request from the first turn added since the previous edit, even if the model edited later. The quote and status line price this, and `edit_ceiling.py` splits the re-read into *format change* and *edit position*.
- **Phases.** Each phase has its own runs folder and gateway ledger. The spend cap ($40) counts all ledgers together.
- **Gate 5 byte check.** `econo get N | sha1sum` runs in the sandbox at the end of each EconoCLM run, after the model's `econo` log is collected (table `get_checks`).

## 2. Results (Raw vs EconoCLM)

_Paste `runs/<D>-main/results.md` (summary and per-run tables)._

## 3. Edit ceiling

_Paste `runs/<D>-main/edit_ceiling.md`: per arm, the share of the bill re-read after rewrites, split into format change vs edit position, plus the append-only background._

## 4. Quote accuracy

_Median absolute error of predicted R vs the actual extra uncached tokens on the next call, in tokens and as % of R (`quote_check.md`)._

## 5. Behavior

- `econo` usage (get, search, sql); fetches of cut outputs
- stale flags, and whether the model re-read those files
- repeated commands; rollbacks; edit counts and where edits happened

## 6. Anomalies

_Usage anomalies by arm ("Usage anomalies (ledger)" in `results.md`; must be 0, else list the rows and their usage), hook errors, failed or `infra_fail` trials, rate-limit waits by arm, anything surprising._

## 7. Plain reading and next step

- Differences of 1–2 passed tasks out of 10, or cost differences under about 20%, can be noise (Gemini's caching is partly random).
- Say which mechanisms visibly fired.
- Recommend one: a 2nd repeat, ablations, skill evolution, BrowseComp-Plus, or cache-aware commits (v2, only if the edit ceiling is about 10% of the bill or more).

## 8. Candidate later arms (noted, not built)

- **Format-preserving rebuild.** Apply the model's edits without flattening the turns it did not touch, keeping their tool-call structure. Then only text from the real edit onward changes on the wire, which removes the *format change* part of the edit ceiling.
  - This changes CLM's mechanics, not just what the model is told, so it is a separate arm.
  - EconoCLM v1 stays information-only: it adds facts to tool results and never changes how CLM rebuilds the context.
  - Worth building only if the format-change share in section 3 is material.
