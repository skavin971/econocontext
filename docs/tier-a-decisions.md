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

- **Boundaries.** `measure/` never imports `econocontext/`. `agents/` and our wrappers in `baselines/` name no provider host or key and reach models only through `gateway/`. A baseline's own code (`baselines/<name>/upstream/`) is its authors'; our wrapper gives it the gateway's api_base.
- **Environments.**
  - transformers goes in `.venv` as the optional `[measure]` extra.
  - The meta-device parameter check runs in `.venv-clm`.
  - A fresh lock for `.venv` is generated at the end of step 4; the old `requirements.lock` is at tag `econo-jev-final`.

## Publishing

- Purdue's internal metadata (user and access-grant ids from `GET /api/models`) is trimmed before any push. `runs/purdue-smoke/models.json` keeps only the model ids and qwen3.8:27b's capabilities.
- Keys live only in `.env`.

## Decisions after the maven runs (2026-10-10)

- **No EconoContext threshold changes yet.** The `harness` settings in `config/v2.yaml` stay as they are, including the 30k compaction minimum, even though Qwen3.8's prompts on maven peaked near 10k. A context profile of all 10 tasks comes first.
- **The same `max_tokens` for every arm: 8,192.** That is our agent's value. CLM's agent gets `max_tokens=8192` through its wrapper (then `agents/clm/run.sh`, now `baselines/clm/run.sh`); its own default is 16,384. Step 10 changes this to 2,048 (below).
  - Duplicate computations are reported per run: `retries` (upstream retries, e.g. after a 600 s timeout) and `repeats` (a prompt sent again identically, e.g. a client's resend).
  - On maven, CLM's call 10 timed out upstream and was resent as call 11.

## Step 9: the first full comparison (2026-10-11)

- **The framing.** FLOPs are compute, not cost; dollar claims come only from real bills (tier C). The question is how raw, EconoContext and CLM compare in real settings on quality (pass rate, overflow rescues) and compute (prefix-reuse FLOPs).
- **Real settings only, no artificial budgets.** The 8k CLM run of step 7 was a measurement test and is not part of results.
- **The arms.**
  - `raw` and `econo+jev`: our agent, at the model's real window. A request over Purdue's 65,536 tokens is refused, and the run is a FAIL (overflow).
  - `clm-32k`: CLM's published setting.
  - `clm-full`: `context_budget_tokens=57344`, i.e. 65,536 − 8,192.
- **Shared settings.** For every arm: `max_tokens` 8,192 and a 64-step limit (our agent's default changed from 60 to 64). Temperatures are as each agent sends them: ours 0.7; CLM 0.7 with `top_p` 0.95.
- **A difference found in the run.** The shell command timeout is 180 s in our agent (reported to the model) and 120 s in CLM's (it stops the run).
- **A gate added overnight.** The runner stops cleanly between trials if the battery is at 20% or less while discharging. The Mac was on battery when step 9 started.

## Step 10: CLM's design for every arm (2026-10-11)

### Why

Step 9 showed that our agent and CLM sample differently:
- CLM: temperature 0.7, top_p 0.95, thinking on;
- ours: temperature 0.7, no top_p.

That may be why ours writes about 2× the output. We now follow CLM's design:
- every method runs zero-shot, on the same model and settings;
- the main setting is a 32,000-token budget for every arm;
- every arm uses CLM's sampling;
- the main comparison is Base, Summary, CLM and (later) EconoContext, all at 32k;
- the repo keeps only what this work needs.

Removed things stay at a tag:
- what came before Tier A is at `econo-jev-final`;
- what was written during Tier A is at `tier-a-step9` (`a4b4a2c`, the last commit before the clean-up).

### Decisions

- **max_tokens 2,048 for every arm (from 10b).** This is CLM's TBLite paper setting, "up to 2,048 generated tokens per call".
  - The gateway pins it on every request and lists the change in `edits`.
  - 8,192 becomes a later sensitivity run.
  - We use CLM's published setting so that CLM is not moved off its own setup. 2,048 cuts our agent's replies more often than CLM's: in step 9, 14–17% of our calls went over 2,048, against 9% of CLM's.
- **A run that hits its limit is graded as it stands (from 10c).** CLM's App. E says: "a request that would exceed it ends the run", and "whenever a run ends … or exceeds the budget, the verifier scores the final state of the container".
  - Our agent stops before the call that would exceed the limit. The limit is budget − 2,048 for Base, and Purdue's 65,536-token window, which the server enforces, for any arm.
  - It records `end` as `budget_exceeded` or `overflow` and returns normally, so the verifier grades the final state of the container.
  - Step 9's three overflows ended in an exception and were never graded. They stay FAILs in tc9's tables.
- **The limit is budget − 2,048.** 2,048 is CLM's `reserve` default. "Over" means P > budget − 2,048, as in CLM's `tc > strict_target`; a prompt exactly at the limit is allowed.
- **Order inside 10a.** The 3 scan-linux r3 reruns of tc9 run first, with tc9's settings: Jev at 2.5 characters per token, CLM's 120 s command timeout and max_tokens 8,192. Only then do Jev 2.3 and CLM 180 s apply.
- **Arm names with a budget.** An arm with a budget is named `<arm>-<N/1000>k`: `raw-32k` (Base), `summary-32k` and `clm-32k`. `clm-32k` is the old name, so it carries over. Old labels such as `clm-full` stay readable.
- **The Summary rebuild (10d).** Codex's compaction prompt is used at 75% of the budget. The conversation then becomes the system prompt, the task, and one user message `{SUMMARY_PREFIX}\n{summary}`.
  - Our nudges are harness messages, not real user messages. Codex keeps only real user messages.
  - CLM's Table 4 says the same: "everything except the system prompt and this task message is then replaced by one message".

### The clean-up (10.0)

The full list is in `docs/cleanup-map.md`.
- **CLM moved.** CLM's code is now in `baselines/clm/upstream`; the submodule keeps its internal name, `third_party/context-language-models`. Our wrapper moved to `baselines/clm/`. Later baselines follow the same pattern: `baselines/<name>/upstream` plus our wrapper.
- **Unused code removed:** `Jev.live`, `Jev.subtask`, `Prior.live`, `Prior.subtask`, `PHASES`, `Session.live_big`, `Prices.usd` and `lifecycle.placement`.
  - `Jev.live`/`subtask`, `PHASES` and `lifecycle.placement` can be recovered at tag `tier-a-step9` if the EconoContext redesign needs the phase question or worker placement.
- **Untracked files moved out of the repo.** Untracked files under the removed paths were never in git, so no tag holds them. They were moved to `~/EconoContext-untracked/`, outside the repo:
  - v0's data;
  - three design documents;
  - logs;
  - Claude Code configs.

### Housekeeping (10a)

- **The push gate also checks Purdue's access-grant fields.** `scripts/check_secrets.py` now fails on any of these appearing as a JSON key in added lines: `access_control`, `group_ids`, `user_ids` and `user_id`. Open WebUI's `GET /api/models` lists them for each model. Inside a call log's JSON strings, the quotes around a key are escaped, so the same words there do not match.
  - The check runs on every commit and, over `econo-jev-final..HEAD`, before every push.
  - It prints file names and counts, never values.
- **Jev's budget is 2.3 characters per Jev token.** That is 28,000 Jev tokens = 64,400 characters.
  - Measured real states gave 2.55–2.67 characters per Jev token. At 2.5, tc9 still had 1 fallback (`max_tokens_exceeded`) in 69 questions.
  - The limit is now computed with `round()`. With `int()`, 28,000 × 2.3 truncates to 64,399, because it is 64,399.99… in floating point.
- **CLM's command timeout is 180 s, passed as a setting.** CLM's harness exposes `command_timeout` as an agent setting (`harness.py:190`, default 120). It is passed to its environment's `exec` as `timeout_sec` (`context_env/env.py:110`).
  - `baselines/clm/run.sh` now passes `command_timeout=180`. That is the paper's TBLite setting ("180 s per shell command") and our agent's value. CLM's code is unchanged.
  - One difference remains, and it is CLM's own design. A timed-out command stops CLM's run, as it did on maven r3 in tc9. Our agent instead reports the timeout to the model.
- **tc9 is complete: 120 of 120 trials made calls.**
  - The 3 scan-linux r3 trials that never started were rerun with tc9's settings, one at a time, after the image was built once. All three passed.
  - The overnight failures came from two workers building the same image at once; Harbor serializes builds only within one process.
  - The failed attempts are kept in `runs/tc9-envfail/`, and the results README notes the reruns.
- **tc9 is the pre-alignment baseline.** It ran with each agent's own sampling (ours: temperature 0.7 and no `top_p`; CLM: 0.7 with `top_p` 0.95 and thinking on), `max_tokens` 8,192 and CLM's 120 s command timeout. It will be re-run under the aligned settings after step 10. Until then, its numbers are not compared with step-10 runs.
- **Stalls are caught from the call log, not the lid.** The runner prints a plain reminder to keep the lid open; it no longer reports the lid state, which reads "closed" in clamshell mode on power while the Mac stays awake. Live runs are watched by a stall monitor, which warns when a trial's `calls.jsonl` gets no new call for 10 minutes.

### The same sampling for every arm (10b)

- **What the gateway pins.** On every chat request, for every agent, the gateway sets:
  - `temperature` 0.7;
  - `top_p` 0.95;
  - `max_tokens` 2,048 (the flag `--max-tokens` sets the later 8,192 sensitivity run);
  - `chat_template_kwargs.enable_thinking` true, keeping the client's other `chat_template_kwargs`;
  - `reasoning_effort` "medium" and `return_token_ids` true, as before.

  A client's `max_completion_tokens` is removed, because vLLM would use it instead of `max_tokens`. Every change is listed in the call's `edits`. The values are CLM's TBLite setting: `SAMPLING` in `gateway/server.py`.
- **Our agent sends the same values itself.** Through the gateway, the only edit on its calls is `return_token_ids`.
- **CLM's agent.** It already sends temperature 0.7, `top_p` 0.95 and `enable_thinking` true. `baselines/clm/run.sh` now passes `max_tokens=2048`, and the gateway adds `reasoning_effort`. A captured tc9 CLM request comes out with the pinned values; its only new edit is `max_tokens` (8,192 → 2,048).
- **Live check (2026-10-11), one call through the gateway in our agent's shape** (`runs/gateway/t10b-live/`):
  - HTTP 200, with a reasoning text and a well-formed `bash` call.
  - Our rebuild equals the server's prompt ids: P 562 = 562, a tools-block key reorder only, ΔP 0.
- **Template probes 7–9** (`scripts/purdue_template_probe.py`, direct to Purdue):
  - With `enable_thinking` true, the server renders exactly what our rebuild renders, whether the flag is true or unset (the template's default is thinking on). This holds for the smoke prompt and for a tool turn without its reasoning.
  - In the control (probe 9), `enable_thinking` false added the empty think block, as our rebuild with false does. So the field reaches the template.
  - In the same control, `max_tokens` 8 was enforced: 8 completion tokens.
- **What "accepted" means for each field:**
  - `enable_thinking`, `reasoning_effort` and `return_token_ids` are seen in the server's prompt ids; `max_tokens` in the completion length.
  - `temperature` and `top_p` were accepted (HTTP 200). Their effect on sampling cannot be seen in single calls.
- **Finding: a reply cut by `max_tokens` is not reported as `length`.** Cut inside a tool call, it came back with `finish_reason` `tool_calls` and the call's arguments lost (`bash` with `{}`; probe 9). With the 2,048 cap, the only reliable sign of a cut reply is `completion_tokens` = `max_tokens`. The gateway log has both.

### Before the budget rule (after 10b)

- **A reply cut by `max_tokens` is served as plain vLLM 0.30 serves it: no gateway change (Kavin, 2026-10-11).** Purdue runs vLLM 0.30.0 (`system_fingerprint` `vllm-0.30.0-…`). vLLM 0.30.0 itself (tag `v0.30.0`, commit `ced6857`) presents a reply cut inside a tool call as `tool_calls`:
  - **The reply is labelled `tool_calls` whenever a call was extracted.** `vllm/entrypoints/openai/chat_completion/serving.py:1031` sets `auto_tools_called = tool_calls is not None and len(tool_calls) > 0`, and `:1071–1090` set `finish_reason="tool_calls" if is_finish_reason_tool_calls else output.finish_reason`. So the label is `tool_calls` even when generation stopped at the length limit.
  - **The parser closes the open call.** When the output ends inside a tool call, `vllm/parser/engine/streaming_parser_engine.py:304–313` (`finish()`) emits `TOOL_CALL_END`. The call is kept, not dropped.
  - **The cut parameter is dropped.** Its arguments are built with `converter(raw_body, False)` (`vllm/parser/engine/parser_engine.py:1092`). With `partial=False`, `_qwen3_arg_converter` (`vllm/parser/qwen3.py:69`) keeps only complete `<parameter=…>` blocks, so a call cut before any complete parameter has the arguments `{}`.

  Purdue's front end hides nothing here; it returns what plain vLLM returns, and that is CLM's serving condition too.
  - Our logs agree. Of the 40 logged replies that reached the 8,192 cap, the 24 without a tool call say `length`. The 16 with a cut call say `tool_calls` and carry the call without its cut parameter, for example `bash` with `{}`, or `write_file` without `content`.
  - Neither agent reads `finish_reason` (CLM's harness never does; ours only for Gemini's `malformed_function_call`). What matters is that a cut call is presented, and both agents run it with its missing arguments. Our `write_file` then writes an empty file.
  - The reports count calls at the cap per run and per arm (`capped`: `completion_tokens` ≥ the request's `max_tokens`). Step 10d shows one cut call per arm and what the agent did next.
- **`top_p` reaches the sampler.** The check was 4 calls direct to Purdue (probes 10–13), all with the same prompt and the run settings: temperature 0.7, thinking on, 256 tokens.
  - At `top_p` 0.01 the two replies were identical for their first 766 characters (of about 1,000).
  - At `top_p` 1.0 the two replies differed from character 115.

  The late difference at 0.01 is consistent with vLLM not being bitwise reproducible across batches.
- **Harbor's output count is `completion_tokens` only.** vLLM's `completion_tokens` already includes the thinking; `reasoning_tokens` is a detail of it. The agent used to add the two together, which was right for Gemini. This was Harbor's own count only; the FLOPs and the reports read the gateway log.

### A budget rule for every arm (10c)

- **CLM's rule, quoted from its released code** (`clm/clm_harness/utils/budget.py`, commit `18dc111`).
  - The module docstring: "when the retained context crosses `budget - reserve` it is a context-limit EVENT: the model gets ONE final turn (submit / write the required output), then the run stops and the sandbox is graded as-is."
  - `self.strict_target = self.budget - self.reserve`, with `reserve_tokens: int = 2048`; the harness's `context_budget_reserve_tokens` is also 2,048.
  - `count()` returns `int(tk.count_tokens(messages)[0] * self.tok_ratio)`. That is tiktoken o200k_base, scaled by a ratio learned from the server's `prompt_tokens` and clamped to [0.5, 3.0].
  - `evaluate()`, when `tc > self.strict_target`:
    - while `self.n_retry_on_limit < self.max_num_retry_on_limit`, it rolls back (CLM's agent passes 50; the controller's default is 0);
    - else, if `self._finalized`, it returns `"stop"`;
    - else it sets `self._finalized = True` and returns `"final_turn"`, appending `final_message(tc)`.
  - Back under the limit, `self._finalized = False`, which re-arms the final turn.
  - The docstring says every harness shares this mechanism, and that one that cannot compact ("e.g. the plain baseline") passes `nudge_ratios=[]`. CLM's Base and Summary harnesses are not in the released repo.
- **Ours (Base = raw with `--budget`).** Before each call, P is the prompt as the server will count it (`measure/tokens.py`).
  - **The rule.** If P > budget − 2,048, the model gets ONE final turn: CLM's notice is appended to the conversation, verbatim, from `agents/react/prompts/clm_final_turn.txt`, with its source and license. Still over at the next check, the run ends with `end` = `budget_exceeded`, and the container is graded as it stands. Back under the limit re-arms the final turn.
  - **No rollbacks.** Base cannot compact, so it uses the controller's default of 0 retries.
  - **Every ending returns normally,** so every run is graded: `submitted`, `max_steps`, `budget_exceeded`, or `overflow` (the server refused a request over Purdue's window).
- **Why we follow CLM's released code over the paper's one line (Kavin, 2026-10-11).** Appendix E summarises Base as "a request that would exceed it ends the run". The code is what CLM ran, and it gives one final turn with a notice. The notice's wording affects behaviour, so we use it verbatim. That is allowed under CC BY-NC 4.0 for non-commercial research, with attribution, and the text is stored in its own file.
- **Where ours differs: the count.** CLM's count is an estimate, calibrated to the server's. Ours is the server's own count, rebuilt with the model's tokenizer, template and tools.
  - On all 1,875 answered tc9 calls (`runs/tier-a/step10c-count.out`), ours equals the server's `prompt_tokens` on 1,432 calls. On 443 it is 1–3 tokens higher, because Purdue reorders tool-schema keys. It is never lower.
  - So our budget check can fire a few tokens early, never late.
- **The arms.**
  - `--budget N` gives every arm in a run the same budget and names it `<arm>-<N/1000>k` (`raw-32k`, `clm-32k`).
  - The `clm` arm needs `--budget` (its `context_budget_tokens`) and replaces `clm-32k` and `clm-full`, whose old labels stay readable.
  - EconoContext arms refuse a budget ("EconoContext has no budget mechanism yet"), in the agent and in the runner.
  - CLM's end reason comes from its `usage.json`: `budget_finalized` gives `budget_exceeded`, and `hit_lm_call_cap` gives `call_cap`.
- **Reports.** A graded run counts by its grade, however it ended, and an `end` column says how it ended. tc9's three overflows were never graded, so they stay FAILs: tc9's tables are unchanged.

### The Summary baseline (10d)

- **The source.** Codex CLI 0.146.0, the version CLM cites ("OpenAI (2026a) … Codex CLI … Software, version 0.146.0"): tag `rust-v0.146.0`, commit `e363b08c9175ac1cbe5893615dd2cb9ddf95043b`. Its licence is Apache-2.0 ("Copyright 2025 OpenAI"), which allows the prompts in our public repo with the licence and notice: `agents/react/prompts/LICENSE-codex` and `NOTICE-codex`.
  - `codex-rs/prompts/templates/compact/prompt.md` (`SUMMARIZATION_PROMPT`) is saved byte for byte in `agents/react/prompts/codex_compact.md`.
  - `summary_prefix.md` (`SUMMARY_PREFIX`) is saved in `codex_summary_prefix.md`.
- **Where the trigger sits: 75% of `--budget`, which is 24,000 at 32,000, not 75% of the 29,952 limit.**
  - CLM App. E: "the summary harness uses the Codex summarization prompts and compacts at 75% of the budget". CLM Table 4: "At three quarters of the budget (about 24.6K of 32,768 tokens) … everything except the system prompt and this task message is then replaced by one message". CLM's own code reads budget fractions the same way: its nudge tiers are `int(self.budget * f)`.
  - Codex's own default is 90% of the model's context window: `ModelInfo::auto_compact_token_limit`, `codex-rs/protocol/src/openai_models.rs:459–469`, "When omitted, core derives it from `context_window` (90%)". CLM's harness sets 75% instead.
  - What is compared is the same in both. Codex compares its active context tokens with `>=` (`codex-rs/core/src/session/context_window.rs`). Those are the last response's `total_tokens` plus a 4-bytes-per-token estimate of the items added since (`codex-rs/core/src/context_manager/history.rs:297–315`), i.e. an estimate of the next prompt. Ours is the next prompt, counted exactly, `>=` 24,000.
- **The compaction call** (`codex-rs/core/src/compact.rs`, `run_compact_task_inner_impl`): the whole history plus `SUMMARIZATION_PROMPT` as a user message.
  - It is sent with no tools: `Prompt { input, base_instructions, ..Default::default() }`, where the default is `tools: Vec::new()` (`client_common.rs`). Ours does the same and is logged with kind `summary`.
  - Without the tools block, the call's prompt shares no prefix with the agent's prompts, so it is prefilled in full. That cost is part of the baseline and shows in its FLOPs.
  - If the server refuses the call for the window, Codex removes the oldest history item and retries (`compact.rs:309–316`). That cannot happen under a 32k budget on Purdue's 65,536-token window.
  - Our agent counts each compaction call before sending it and reports the count; the budget is not enforced on it.
- **What Codex keeps** (`compact.rs`, `build_compacted_history_with_limit`):
  - the most recent real user messages up to `COMPACT_USER_MESSAGE_MAX_TOKENS` = 20,000 approximate tokens (the oldest one kept is truncated); earlier summaries are excluded (`is_summary_message`);
  - then one user message, `"{SUMMARY_PREFIX}\n{summary}"`, where the summary is the last assistant message's text. "(no summary available)" appears only if the whole text is empty, which never happens in local compaction.

  Codex re-inserts its initial context (developer and environment instructions) before the last real user message, and sends the base instructions (the system prompt) with every request.
  - **Ours:** `[system, task, "{SUMMARY_PREFIX}\n{summary}"]`. The task is our one real user message and is far under 20,000 tokens; a longer one would raise an error. Our nudges and CLM's final-turn notices are the harness's, like Codex's injected context, so they are not kept. This matches CLM's Table 4.
- **After compacting, the Base rule applies.** If the rebuilt prompt is still over budget − 2,048, the model gets one final turn and the run ends.
- **What each compaction reports:**
  - the prompt before it, and the compaction call's own prompt;
  - the summary's output tokens and whether it hit the 2,048 cap (a capped summary stops the run, through a runner gate);
  - its finish reason;
  - the rebuilt prompt, with its share of the budget.
