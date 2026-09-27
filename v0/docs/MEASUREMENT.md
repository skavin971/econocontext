# Measurement and prediction

Every actual model/tool attempt has a stable ID, timestamps, monotonic elapsed time, worker/session, optional operation/plan, phase, request/response references, status, retry link, raw usage and normalized accounting. Ordinary root calls and external baseline calls may have no operation or plan. A repeated completion notification cannot add another charge.

## Cost and unknowns

Supported Chat Completions usage splits total prompt tokens into uncached input and reported cache reads, then adds completion tokens. Providers disagree on whether `completion_tokens_details.reasoning_tokens` is already inside `completion_tokens`: OpenAI includes it, Google's OpenAI-compatible endpoints report it separately and exclude it. The provider's own `total_tokens` disambiguates — reasoning is added to billed output only when `prompt + completion + reasoning` equals the reported total, so it is never charged twice. A provider that omits `completion_tokens` on a turn that produced no content has its output derived from the same identity. Invalid or negative categories are rejected as incomplete, never silently clamped. Raw provider-specific details are retained. Separately priced cache-write extensions require a provider-specific normalization extension before they can be treated as known charges.

Missing usage stays unknown. When total usage exists but cache detail is absent, normalized input is shown without a cache discount, `cache_reported` is false, and the whole prompt is charged at the uncached rate. That is an upper bound rather than a refusal: excluding such calls from run totals understated measured Gemini runs by roughly 2.5x, because that endpoint omits `prompt_tokens_details` entirely on calls where nothing was cached. Failed requests can incur unknown charges. Reports show known subtotals separately from completeness; a zero known subtotal does not imply a free run.

Rates are explicit versioned per-million token prices. Live runs without prices retain usage but no invented dollar total. Their fixed fallback order is REUSE, CONTINUE, then FRESH; this is reported as not cost-optimized. Monetary preparation cost defaults to zero (unpriced local work); measured local overhead is reported separately, not silently converted to dollars.

Run totals sum unique actual attempts, not nested worker/operation subtotals. The external fixture verifier is tagged `external_grader` and excluded from agent cost. Tests invoked by the agent remain agent tool attempts. Run wall time is start-to-finish elapsed time, not the sum of attempt durations.

## Operation prediction boundary

The estimate separates preparation, execution, initial parent integration, and additional work. It charges retained input on each predicted call and allows for accumulating output in later inputs. Latency is in seconds and never added to money.

Operation execution ends at the structured result. For delegated/reused results, the next successful root response and its retries belong to initial integration. Later root work is ordinary work or another operation. Inline root continuation has no artificial integration call. Each actual attempt has one owner.

The metrics endpoint reports execution-only and integration-inclusive known cost, attempt durations, completeness, and cost prediction error when units are comparable. Attempt durations are explicitly labeled; run wall time remains separately measured. Planning and assembly overhead is reported from local events.

## Profiles and cache estimates

`econocontext profiles RUN_ID ... --output profiles.json` builds frozen profiles from completed successful calibration runs. Keys include adapter, operation kind, mode, view, input-size bucket and model/configuration fingerprint. Profiles retain sample count, latency dispersion, base call/output counts, and retry frequency multiplied by measured incremental retry cost. Retry attempts are excluded from the base call count to avoid counting them twice. Other evidence acquisition remains part of measured base operation execution.

Compatible system/tool-prefix fingerprints collect observed cache fractions. With at least three matching samples the estimator gives conservative cache credit capped at 10% of input and at the compatible prefix size. Without observations, expected cache credit is zero. This is an uncertain prediction, not proof that a particular retained session is cached. Fresh workers can share prefixes; continuing workers can miss.

Sparse default call counts, output sizes and latency are assumptions, not measurements. Scripted profiles and usage are synthetic. Calibration samples describe executed selections only; unselected candidate outcomes are unknown. Keep held-out evaluation data out of calibration and freeze profiles before comparing methods.

Neither cache reads nor observed misses reveal physical KV eviction. No engine instrumentation or physical cache-residency claims are implemented.

## External runner integration

`Telemetry` can be used without the planner. Create a run/session; call `Telemetry.begin` immediately before each actual model/tool attempt and `finish` after success, failure or cancellation. Supply stable IDs for replayable ingestion. Preserve raw usage and mark missing usage as unknown. Disable hidden retries in the external client or wrap each underlying attempt separately. No ACM checkout is assumed.

## Endpoint contract checked

The implementation uses non-streaming Chat Completions, tool-call IDs, environment credentials, configurable output-limit field, and optional prompt-token cache details. It uses httpx without hidden request retries. Compatibility is endpoint-specific; contract tests use a mock HTTP transport, not a paid model.

References checked during implementation: [OpenAI Chat API reference](https://developers.openai.com/api/reference/resources/chat), [SQLite WAL](https://sqlite.org/wal.html). No live pricing was assumed.
