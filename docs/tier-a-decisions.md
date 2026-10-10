# Branch tier-a-purdue: decisions (Kavin, 2026-10-10)

These are the settings the Purdue/Qwen3.8 measurement depends on, with the evidence behind each. The evidence is in `runs/tier-a/` and `runs/purdue-smoke/`.

## The model and the template

- **The model.** `qwen3.8:27b` on Purdue GenAI Studio is `Qwen/Qwen3.8-27B-FP8` at revision `017b9c7`. Rendered with its own tokenizer and chat template, our request is identical, token for token, to the server's prompt (step 1).
- **Eq. 7.** Model constants (CLM Eq. 7) come from the architecture fields of `Qwen/Qwen3.8-27B-FP8`'s config. FP8 weights don't change FLOP counts.

## How every prompt is rebuilt

1. **Parse the messages as vLLM 0.30 does.**
   - Assistant turns keep `content`, `tool_calls` and `name`.
   - Tool-call `arguments` strings are JSON-parsed into dicts (invalid JSON or a non-object becomes `{}`).
   - Reasoning is read only from a `reasoning` field.
   - Tool messages keep `tool_call_id`.
2. **Render** with the HF template, `reasoning_effort="medium"` and `preserve_thinking` unset.

## Earlier reasoning is never resent: a deliberate setting

- **What the gateway does.** It removes `reasoning` and `reasoning_content` from every earlier assistant message before forwarding upstream, for every agent and every provider. The rebuild renders the same prompt, with no earlier reasoning, so it needs no field-name emulation.
- **Why.** This matches CLM's standard-serving regime, and it matches what Purdue's stack does anyway:
  - vLLM 0.30 drops `reasoning_content`;
  - Purdue's front end strips `reasoning` too (step-1 template probe).
- **Later ablation.** Interleaved thinking (`preserve_thinking`, earlier reasoning resent) is a possible ablation, for all arms alike.

## Pinned request fields (gateway)

- `"reasoning_effort": "medium"` on every upstream request, so the rebuild never depends on a server default. Purdue accepts it as a top-level field, and `"low"` was shown to change the server's prompt.
- `"return_token_ids": true` on every upstream request. The response then carries the server's exact `prompt_token_ids`.

## How the gateway behaves (step 3)

- **Rate limit.** At most 20 upstream requests in any rolling 60 s. Every upstream attempt counts, retries included (tested). Callers wait; they are never refused.
- **Retries.** A JSON `null` or empty body, a 429, a 5xx, or a timeout or connection error is retried with backoff (2, 4, 8, 16, 32 s), 6 tries at most. The count goes in `retries`.
- **Caps.**
  - The defaults are 150 calls per run and 8,000 requests per UTC day.
  - `/health` reports both caps and today's count. The runner starts a trial only if the day still has a full run's calls left (150) after reserving 150 for every trial already running. So no run is cut off midway, as 9 Gemini trials were on 2026-10-06.
  - A refusal is an HTTP 429 whose message contains "reached", so our agent stops.
  - A refusal is logged with `call_no` null.
  - The counts are rebuilt from the log when the gateway restarts.
- **Earlier reasoning.** Removed from every assistant message: `reasoning`, `reasoning_content`, and the whole `provider_specific_fields` key (our agent echoes it, and it carries a copy of the reasoning).
- **The log.**
  - One line per call, with `run_id`, `call_no`, `kind`, `provider`, `model`, `t_start`, `latency_ms`, `limiter_wait_ms`, `status`, `retries`, `edits`, `request` (exactly as sent upstream, after the gateway's edits), `response` (exactly as returned) and `usage`.
  - `X-Econo-Call-Kind` is ours: it is logged, never forwarded (tested).
  - The key never appears; it is redacted if a provider ever echoes it.
  - Model-list calls are not logged.
- **Streaming.** Neither agent streams: ours sends no `stream`, and CLM's `litellm.completion` call sends none either (checked through the gateway). A `stream: true` request gets a 400.
- **CLM's requests.** CLM's agent also sends `top_p` and `chat_template_kwargs: {"enable_thinking": true}`. The rebuild uses a request's `chat_template_kwargs` as sent.
- **No default upstream.** Our agent refuses to start without an `api_base`. The isolation test checks that every client in `agents/` takes its `base_url` from the `api_base` it was given, and that nothing there reads the SDK's base-URL settings.

## What the FLOPs are computed on

- **Headline:** our request, rendered as above. This is an ideal vLLM server that receives exactly what we send.
- **Beside it,** labelled *"as served by Purdue (includes front-end key reordering); informational, not for comparing arms"*: the same computation on Purdue's actual prompt ids. Purdue's front end randomly reorders tool-schema keys on most requests, which breaks the real prefix cache inside the tools block for reasons that have nothing to do with any agent setup.

## Summary calls (EconoContext's compaction summaries)

- **Headline.** Summary calls stay in the run's cache sequence, in order with the agent calls. This is realistic: their prompt is the agent's conversation plus one request, so it shares the agent's prefix on a prefix-caching server.
- **Reported apart.**
  - Each run's summary-call FLOPs, on a line of their own.
  - A CLM-convention variant. Summary calls are charged as a full prefill with no cache hit, and they don't enter the cache, as CLM's code treats auxiliary calls (`aux_prefill_tokens`). The difference between the two conventions is then visible.

## The measurement as built (step 4)

- **Constants.** From Qwen/Qwen3.8-27B-FP8's config (Eq. 7): C_token = 48,701,112,320 and C_attn = 393,216.
  - The text config is identical to Qwen3.6-27B's, field by field, so C_token and C_attn equal CLM's (Appendix C).
  - **Tier B** will serve the same Qwen3.8-27B-FP8 on our own vLLM, to validate the cache simulation on the identical model. Qwen3.6-27B is for comparisons with CLM.
  - Counting the real linear layers on the meta device gives the same C_token exactly.
  - CLM's code table (2 × 24.3532e9) is 0.011% higher because it also counts the 2.65M non-matmul body parameters: short convolutions, norms, and the DeltaNet's A and dt.
- **Cache simulation vs CLM's code.** On 60 seeded synthetic runs, ΣP, Σ(P − R) and the prefill attention pairs are exactly equal, except in runs with identical repeats. There our extra uncached tokens equal Σ(P mod 16) of the repeats, exactly.
- **Decode.** Decode attention differs by design: Eq. 9 uses each call's G_t, while CLM's code averages generation over turns. On the synthetic runs the gap is a median of −9.3% of the decode term (range −26.8% to +7.4%), but only −0.087% of total F (range −0.37% to +0.05%).
- **Validation on the saved Purdue prompts** (11 with the server's own ids, steps 1 and 3):
  - 2 are identical;
  - 7 differ only in the tools block (ΔP 0 or −1);
  - the `reasoning` probe is caught;
  - the step-3 live call is rejected: Purdue reordered the keys of three tool schemas there (ΔP −2).

  Purdue reorders some tool's keys in 9 of 11 requests, so the actual-ids variant will see the real prefix cache break inside the tools block on most calls.

## Validation (rule as of 2026-10-10, step 4 review)

Each rebuilt prompt is compared with the server's ids, token by token. A mismatch is accepted only when all of these hold:
- it is confined to the tools block;
- the tools are the same JSON objects, ignoring key order;
- everything after the tools block matches token for token;
- the text before the tools block matches exactly.

It is accepted at any ΔP, and ΔP is recorded per call. Anything else stops the measurement for review.

The first rule, |ΔP| ≤ 1, was replaced: the one real gateway call already had three tool schemas reordered (ΔP −2).

## Prefix cache (simulated)

- **Rule.** Only full 16-token blocks count. A block is identified by a chained hash, and blocks are matched against earlier prompts of the same run, which starts empty.
- **vLLM's last-token rule** (added 2026-10-10): R = min(R, 16·⌊(P − 1)/16⌋). A fully cached prompt still recomputes its last block.
- **Difference from CLM's code.** CLM's code matches a trailing partial block and has no last-token rule. So it gives R = P for a prompt fully covered by earlier ones, where we give:
  - for an identical repeat: P − (P mod 16), or P − 16 when P is a multiple of 16;
  - for an exact earlier prefix ending on a block boundary: P − 16.

  The cross-check predicts this gap per prompt, and it matches exactly. On 60 synthetic runs there are 0 other differences.

## The call log

- `kind`: compaction summary calls carry `X-Econo-Call-Kind: summary` (sent by the agent; `owner.py` is unchanged).
- Run ids use only `[A-Za-z0-9._+-]`.

## Boundaries and environments

- **Boundaries.** `measure/` never imports `econocontext/`. `agents/` names no provider host or key and reaches models only through `gateway/`.
- **Environments.**
  - transformers goes in `.venv` as the optional `[measure]` extra.
  - The meta-device parameter check runs in `.venv-clm`.
  - A fresh lock for `.venv` is generated at the end of step 4; the old `requirements.lock` is archived.

## Publishing

- Purdue's internal metadata (user and access-grant ids from `GET /api/models`) is trimmed before any push. `runs/purdue-smoke/models.json` keeps only the model ids and qwen3.8:27b's capabilities.
- Keys live only in `.env`.
