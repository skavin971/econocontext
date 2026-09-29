# 4. Measurement and data

A decision is only useful if its effect can be measured. This chapter covers how
real costs are captured, and what the database holds.

## Every model call is measured, in both arms

Every agent's model URL points at the gateway (`omnigent_layer/gateway.py`), so it sees
**every** model call, whichever harness or agent makes it. For each call it:

1. **Checks the caps**, and refuses the call past 60 per run or 3M input tokens per day.
2. **Plans (econo arm only):** turns the request into segments (`omnigent_layer/wire.py`) and calls `plan_prompt`. Baseline requests are forwarded byte for byte.
3. **Forwards** the request to Vertex with the real key, and relays the reply (streamed line by line).
4. **Records:** the reply's usage is translated into a `ProviderUsage` (`wire.to_usage`) and passed to `engine.record`, linked to the `plan_prompt` decision that shaped the call. Predicted and actual can then be joined.

- **Same measurement in both arms:** the gateway measures both arms the same way.
- **Logging:** each call also gets a line in `logs/gateway/calls.jsonl` (path, arm, status, usage), with no prompt text.

## What the provider reports (Vertex's OpenAI-compatible endpoint)

Checked against live replies on 2026-09-28 ([findings](omnigent-findings.md), question 0b):

| `ProviderUsage` field | Comes from | Note |
|---|---|---|
| `uncached_input` | `prompt_tokens − cached_tokens` | Tokens sent fresh |
| `cache_read` | `prompt_tokens_details.cached_tokens` | Served from the implicit cache at 90% off. **Absent** when nothing was cached: recorded as `None` ("not reported"), with the whole prompt as uncached |
| `cache_write` | always `None` | Gemini's implicit cache has no separate write charge |
| `output` | `completion_tokens + reasoning_tokens` | Vertex reports reasoning **outside** `completion_tokens` (prompt + completion + reasoning = total). If they don't add up, reasoning is assumed to be inside already and is not added twice |
| `reasoning` | `completion_tokens_details.reasoning_tokens` | For explanation |
| `raw` | the whole usage dict | Kept for audit |

`None` always means "not reported", never zero.

## Turning usage into money: the ledger (to build)

`econocontext/pricing/ledger.py` receives every `ProviderUsage`:

- **Today it is a skeleton.** It saves the token counts to the `outcomes` table and leaves the cost columns empty. The spec for building it is in the file's docstring. Its acceptance tests are in `tests/unit/test_ledger.py`; they are skipped until it exists.
- **Once built:**
  - cost = each billing category × its price from `config/billing_rates.yaml`, in USD and NU
  - total run cost = a SQL sum over `outcomes`
  - `report.py` then prints it
- **Until then, dollar budgets can't stop a run.** `run_usd` returns 0, so only the step limit caps a paid run.

**Price cards** (`config/billing_rates.yaml`) hold one card per model:

- **What a card holds:** the source URL, the date it was read, *periods* (prices that change on a date) and *tiers* (prices that depend on prompt size).
  - Gemini 3.6 Flash has two periods: a promotional price until 2026-12-31, and double that from 2027-01-01.
  - Unverified fields are `null # TODO: verify`.
- **Which cards exist:** Gemini 3.6 Flash (the model in use), Claude Sonnet 5, Claude Opus 5.5, gpt-6-sol and gpt-6-astra.
- **How they are used:** `pricing/rates.py` turns a card into NU ratios. For Gemini: output 5.0, cache read 0.1.

## The Agent DB

One SQLite file, `data/econocontext.sqlite3` (gitignored, created on first run).
Schema and comments: `econocontext/store/schema.sql`.

| Table | One row per… | Used for |
|---|---|---|
| `runs` | Run of one instance in one arm | Arm, mode, model, temperature, config fingerprint, `jev` on or off, status, official pass/fail |
| `agents` | Agent or subagent | The agent tree (parent, type, status) |
| `segments` | Piece of context any agent ever saw | The full text, kept forever. Written once, never changed; identity = agent + message id + content hash |
| `window_entries` | Segment in an agent's current window | Position, zone, representation (FULL or POINTER). This is the part that changes |
| `segments_fts` | (search index) | Keyword search over segment text, used by RETRIEVE_FROM_STORE |
| `source_versions` | File path (and `*`) | The current version; bumped by the write barrier |
| `tool_results` | Tool call | Tool name, argument hash, read set, side effect, valid. Used for ANSWER_FROM_STORE |
| `stored_results` | Subagent task | Task key, result text, read set, valid. Used for REUSE_RESULT |
| `decisions` | Decision at any intercept | Every candidate's predicted cost, what passed the gates, what was chosen, whether it was applied, `why_not` for each loser, time taken, any error, and the `p_need_again` used (with its source: prior or Jev) |
| `outcomes` | Physical model call | The reported tokens by category, latency, phase, cost (once the ledger is built), and the raw usage |

## The closed loop: predicted next to actual

- **Joined on `decision_id`:** a `plan_prompt` decision stores its predicted cost, and the model call it shaped stores the actual usage. `summary()` in `pricing/ledger.py` compares the two per run.
- **Cache prediction too:** `cache_belief` predicts cached tokens before each call, and the provider reports the real number after it. The running ratio (`observed_hit_ratio`) is kept per agent.
- **Why it matters:** this is the data a smarter predictor learns from later (chapter 6).

## Reading results

```sh
.venv/bin/python bench/report.py --label q6b
```

For each run, the report prints:
- status and whether it resolved
- model calls (and how many were summarization)
- tokens by category, and the cache-read share
- cost (once the ledger is built)
- predicted vs actual
- decisions by operator (`*` = carried out)
- the "would-be" counts: how often each non-default operator passed every gate

Then it prints totals per arm. `econo` and `econo+jev` runs are always listed separately.

For anything else, query the DB directly:

```sh
sqlite3 data/econocontext.sqlite3 "SELECT chosen, why_not FROM decisions WHERE run_id LIKE 'check5:econo%' LIMIT 5"
```

Next: [5. Omnigent and SWE-bench](5-omnigent-and-swebench.md)
