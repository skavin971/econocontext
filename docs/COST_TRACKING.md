# Cost tracking, runtime timing, and reporting

## 1. Purpose and terminology

EconoContext keeps provider accounting separate from planning. The planner
estimates what a choice may cost; the ledger records what the provider reported
after a model call finishes.

- **Actual cost** is reported provider usage priced with the applicable local
  rate card.
- **Predicted cost** is the pre-call estimate stored with a decision.
- **Running cost** is the known actual subtotal accumulated so far.
- **Incomplete cost** means at least one billed counter was unavailable. The
  known subtotal is retained, but the total is not treated as reliable.
- **NU** (normalized units) expresses usage relative to one uncached input token
  for the configured model. USD is the published-price representation.

## 2. Gemini usage mapping

`adapters/providers/gemini_usage.py` maps Gemini usage metadata as follows:

| Gemini field | Ledger category |
| --- | --- |
| `input_tokens - input_token_details.cache_read` | `uncached_input` |
| `input_token_details.cache_read` | `cache_read` |
| No separately billed implicit-cache write field | `cache_write = None`, `cache_write_applicable = False` |
| `output_tokens` | `output` |
| `output_token_details.reasoning` | `reasoning` for explanation only |

Gemini's `output_tokens` already includes reasoning tokens, so reasoning is not
charged a second time. The adapter preserves missing counters as `None`; it only
maps Gemini's known implicit-cache write behavior to a non-applicable write term.

## 3. Cost formula and NU normalization

For one call, rates are USD per one million tokens:

```text
USD = (uncached input × standard input rate)
    + (cache write × cache-write rate)
    + (cache read × cached-input rate)
    + (output × output rate)
```

The sum is divided by 1,000,000. NU applies the same relative prices:

```text
NU = uncached input
   + cache write × cache-write ratio
   + cache read × cache-read ratio
   + output × output ratio
```

For Gemini implicit caching, the cache-write term is zero because it is not a
separately applicable billed counter. Missing required counters produce
`cost_complete = false` and null cost values; missing usage is never treated as
free usage.

## 4. Price-card periods, tiers, provenance, and cache semantics

`config/billing_rates.yaml` is a versioned local price-card catalog. The ledger
selects the period from the call date and the tier from the reported prompt
size. It stores the applied period identifier in `outcomes.price_period`.

Each card records the provider, model, publisher source URL, retrieval date,
effective periods, prompt tiers, and cache rates. Reports include this
provenance and the configuration fingerprint. Gemini's current card represents
implicit-cache reads; separately billed explicit-cache writes remain future
work.

## 5. Model-call lifecycle from callback to `outcomes`

The shared LangChain callback is installed in both experiment arms:

```text
on_chat_model_start
  → attribute the call to the root or nearest task subagent
  → check the configured budget
  → open a model runtime span

on_llm_end / on_llm_error
  → map Gemini usage into ProviderUsage
  → calculate and store actual cost in outcomes
  → close the model runtime span
```

`outcomes` is the durable source for token totals and actual cost. It retains
the normalized provider usage in `raw` for auditability and keeps failed or
incomplete attempts. `outcomes.latency_ms` remains the model-call duration for
backward compatibility.

## 6. Runtime spans and active-agent tracking

`runtime_spans` records `model`, `tool`, and `dispatch` lifecycles with start/end
timestamps, duration, status, native IDs, decision IDs, and JSON metadata. Open
rows represent work currently in progress; failed rows are retained. A process
that terminates unexpectedly can therefore leave an open row for later audit.

Model spans come from the shared callback. Optimized-arm tool and dispatch spans
come from `EconoMiddleware`; baseline tool and dispatch spans come from the
same callback around host lifecycle events. `ANSWER_FROM_STORE` and
`REUSE_RESULT` have no physical execution span.

An agent is `busy` while it has one or more open spans and returns to `idle` only
after its final concurrent span closes. `retired` remains reserved for explicit
future lifecycle support.

## 7. Predicted, actual, running, and incomplete cost

These values must not be conflated:

- `predicted_cost`: EconoContext's pre-call plan estimate, currently expressed
  in NU.
- `actual_cost`: the complete total from provider counters and the stored rate
  card; it is null as a reliable total if any outcome is incomplete.
- `running_cost`: known actual cost accumulated so far, including the known
  subtotal of a partially accounted run.
- `incomplete_cost`: a flag indicating that the running subtotal is not a fully
  reliable bill.

Before each model call, known spend is checked against the configured budget. If
any earlier outcome is incomplete, the next model call is refused with a clear
budget-unknown error rather than treating that outcome as zero. The configured
step limit remains the fallback safety cap.

## 8. SQLite tables and example queries

SQLite remains the canonical store. The database is normally
`data/econocontext.sqlite3`.

Running cost by agent:

```sql
SELECT run_id, agent_id, SUM(cost_usd) AS known_usd,
       SUM(cost_nu) AS known_nu, SUM(uncached_input) AS uncached_input,
       SUM(cache_read) AS cache_read, SUM(output) AS output
FROM outcomes
GROUP BY run_id, agent_id;
```

Currently active work:

```sql
SELECT run_id, agent_id, kind, name, started_at
FROM runtime_spans
WHERE status = 'open'
ORDER BY started_at;
```

Known versus incomplete cost:

```sql
SELECT run_id, SUM(cost_usd) AS known_usd,
       SUM(CASE WHEN cost_complete = 0 THEN 1 ELSE 0 END) AS incomplete_calls
FROM outcomes
GROUP BY run_id;
```

## 9. Text, JSON, and CSV report commands

Text remains the default human-readable format:

```sh
python scripts/report.py --label check1
```

JSON is the timeline-ready export for notebooks or later visualization:

```sh
python scripts/report.py --label check1 --format json --output report.json
```

CSV is one flattened row per run for spreadsheets and statistical analysis:

```sh
python scripts/report.py --label check1 --format csv --output report.csv
```

JSON includes run metadata, pricing provenance, price periods, configuration
fingerprints, calls grouped by phase and agent, token/cost totals, timing spans,
decisions, predictions, and resolution status. CSV includes explicit actual and
running cost fields. Neither export includes prompts, tool outputs, or raw
provider payloads; those remain in SQLite.

## 10. Recommended benchmark metrics

For historical comparisons, track:

- cost per completed task;
- cost per successful task;
- wall-clock duration;
- model latency;
- tool time and dispatch time;
- cache-read share;
- total model calls;
- predicted-versus-actual cost error.

A cheaper unsuccessful run is not an improvement. Cost and duration should be
reviewed alongside task resolution and quality results.

## 11. Comparing historical runs with online pricing

Use versioned local price cards for historical reports. Preserve the publisher
source URL, retrieval date, effective period, and configuration fingerprint.
Never fetch mutable online prices while rendering a historical report.

To compare a historical token trace with another model or price period, replay
the recorded billing categories against a separate price card and label the
result **simulated** or **hypothetical** cost. It is not the actual billed cost
of the original run.

## 12. Explicit limitations

- Gemini implicit cache writes are not separately observed or charged.
- Provider counters can be incomplete; those calls are not silently priced as
  zero.
- Estimates are not guarantees and are not learned forecasts in this phase.
- Alternate-provider mappings and explicit-cache behavior are future work.
- A process crash can leave runtime spans open until a later cleanup or audit.
- No plotting dependency is added; JSON and CSV are the visualization boundary.
