# Running the experiments

How to reproduce what EconoContext does, read what it prints, and judge whether
it worked. Start offline — it costs nothing and shows the whole mechanism.

## Setup

```sh
cd mvp
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
pytest -q          # 27 passed
```

## 1. Watch a run, one component at a time (free)

```sh
ECONOCONTEXT_BACKEND=scripted ECONOCONTEXT_MODEL=scripted-v1 ECONOCONTEXT_BASE_URL=x \
  econocontext --data-dir /tmp/demo run --fixture corpus --adapter research --step
```

`Enter` advances one handoff, `c` runs to the end, `q` stops narrating. Each
block names the component that produced it, so the run reads as a sequence:

```
STEP 4  ASSEMBLER  build request  [root]      the exact prompt, in full
STEP 5  BACKEND  model call  [root]           tokens, cache share, cost, the tool call
STEP 6  PLANNER -> CANDIDATES -> COST MODEL -> OPTIMIZER
                                              every candidate with its estimate
STEP 9  MANAGER  plan committed
STEP 10 ASSEMBLER  build request  [child-1]   the child's own prompt
STEP 13 ASSEMBLER  build request  [root]      root receives only the finding
```

Step 13 is the point of the system: the child's reading never enters the root.

**This uses a fake model.** `scripted` is an `if/elif` chain, not an AI, and it
is hard-coded to delegate. It shows the machinery; it proves nothing about a
real model's choices.

## 2. Point it at a real model

Put credentials in `mvp/.env` (gitignored). For Google's OpenAI-compatible
endpoint, verified working:

```sh
ECONOCONTEXT_BACKEND=live
ECONOCONTEXT_BASE_URL=https://aiplatform.googleapis.com/v1/projects/PROJECT/locations/global/endpoints/openapi
ECONOCONTEXT_MODEL=google/gemini-3.5-flash
ECONOCONTEXT_AUTH_HEADER=x-goog-api-key      # NOT Authorization: Bearer
ECONOCONTEXT_AUTH_SCHEME=
ECONOCONTEXT_OUTPUT_PARAMETER=max_tokens
ECONOCONTEXT_CREDENTIAL_ENV=MY_API_KEY
MY_API_KEY=...
ECONOCONTEXT_PRICING={"revision":"2026-09-16","input_per_million":1.50,"cached_per_million":0.15,"output_per_million":9.00}
ECONOCONTEXT_CALL_LOG=./llm_calls.log
```

`ECONOCONTEXT_CALL_LOG` appends every request and response in full, with
per-call latency and token split. Delete it to turn logging off.

Sanity check before spending:

```sh
econocontext run --fixture corpus --adapter research --max-cost 0.05
```

## 3. The A/B experiment

Both arms, identical settings, one variable — the policy:

```sh
econocontext run --fixture ledger --method econocontext \
  --context-tokens 32000 --plan-pressure 0.2 --output-tokens 2048 \
  --max-attempts 60 --retries 3 --max-cost 1.00

econocontext run --fixture ledger --method react \
  --context-tokens 32000 --plan-pressure 0.2 --output-tokens 2048 \
  --max-attempts 60 --retries 3 --max-cost 1.00
```

`ledger` is five modules of ~2,250 tokens each with four independent defects,
one per module, and one test whose failure points at a *different* module than
its cause. Hidden verification uses different inputs than the visible tests, so
passing cannot be faked.

The two knobs that decide whether delegation is reachable at all:

- **`--plan-pressure`** — fraction of the context budget at which delegation is
  offered. At the 0.5 default with a 32,000 budget, nothing is offered until
  16,000 tokens, which a short run never reaches. Delegation then never fires
  and the two arms differ only in prompt wording.
- **`--observation-tokens`** (default 512) — the floor below which an
  observation is not worth an operation. A finding cannot be cheaper than a
  three-line file.

## 4. Reading the result

```sh
econocontext explain RUN_ID --output walkthrough.txt
```

The numbers that matter, and where they come from:

| What | Where | Means |
|---|---|---|
| `verification: verified` | run record | hidden tests passed — the only pass/fail |
| `MANAGER answered with a bounded view` | explain | a delegation actually delivered |
| `inline_tokens -> delivered_tokens` | same block | root context spared |
| `MANAGER optimisation abandoned` | explain | a delegation was tried and dropped, with the reason |
| `known_cost` + `cost_complete` | metrics | spend; `false` means some calls had unknown usage |
| `comparisons[].cost_error` | metrics | predicted minus actual — whether the cost model is right |

```sh
econocontext export RUN_ID --output report.json    # full trace as JSON
```

## What a real run produced

Gemini 3.5 Flash, ledger fixture, 32,000-token context, one run per arm:

| | econocontext | react |
|---|---|---|
| Result | **succeeded / verified** | **failed** |
| Why | — | context exceeded: 31,598 + 2,048 > 32,000 |
| Root peak | 25,787 tokens | hit the 32,000 wall |
| Cost | $0.2622 | $0.1901 |
| Delegations | 2 delivered, 1 abandoned | n/a |
| Root context spared | 3,305 tokens (54% per swap) | n/a |

**Read this carefully.** react was *cheaper* — because it died. The comparison
the research asks for is cost at *matched quality*, and quality is not matched
when one arm fails. The honest claim from this pair is narrow: at a 32,000-token
budget this task is out of reach for the flat agent, and delegation brought the
root's peak low enough to finish for $0.26.

It is also **one run per arm**. Treat it as an existence proof, not a
measurement. Delegation costs an extra child call plus an integration call, so
on tasks that fit comfortably it should be expected to cost *more* — that is the
trade the cost model exists to price, and `cost_error` is what says whether it
prices it correctly.

## Known rough edges

- A child sometimes answers in prose instead of calling its completion tool.
  That wastes a delegation; the run continues on the literal result.
- `apply_patch` messages are the root's own output and no representation choice
  can shrink them — about 21% of context growth in measured runs.
- Context already accumulated cannot be reclaimed. V1 bounds what enters next;
  compaction is not implemented.
