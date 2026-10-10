# EconoContext — brainstorm notebook

---

# ▶ STEP 7 · REVISION (2026-09-25) — skip Claude, get past Gemini throttling

## Context

Stages 0–3 of Step 6 are built and smoke-tested (39 tests green, $0.41 spent on
13 smoke runs). Two things changed:

- **The user dropped Anthropic models for now.** The Claude backend stays in the
  code, covered by its contract test, but no Claude run is made.
- **Gemini 3.5 Flash is mostly refused (429 "Resource exhausted").** The quota
  rows the user pasted are resource-management and Compute Engine limits, not
  serving quotas. Gemini 3.x pay-as-you-go runs on Dynamic Shared Quota: a shared
  global pool with no per-project number to raise. A clean probe (nothing else
  running, one ~4K-token request every 20 s) was refused 5 of 6 times, and
  Gemini 3.5 Flash has no regional endpoint on this project. Only Provisioned
  Throughput guarantees capacity.

Model-agnosticism still needs at least two caching regimes that actually run.

## Step A · Throttle probe (≤ $0.50, before any pilot)

The user chose **Gemini 3.6 Flash** as the cached model, replacing 3.5 Flash.

1. **Access check:** send one ~10-token request to `google/gemini-3.6-flash`.
   If that id is rejected, find the right one from the error or the Model Garden
   name, and stop to report if the model isn't available.
2. **Throttle probe:** `research/live_study/probe.py` sends the recorded
   realistic request (the 14 KB, 12-message SWE turn from smoke-policies-3)
   10 times, 20 s apart, and records the admit rate, latency and cost.
   Rule, fixed before the probe: the model is usable if at least 7 of 10
   requests are admitted within the harness's retry budget (8 retries, backoff up
   to 60 s). If it fails the rule, stop and report; don't fall back silently.
3. **Rates, from the Vertex sheet read 2026-09-25:**

| Model | $/M in · cache hit · out | Note |
|---|---|---|
| Gemini 3.6 Flash | 0.75 · 0.075 · 3.75 | promotional through 2026-12-31; 1.50 · 0.15 · 7.50 from 2027-01-01. The pricing revision records which applied. |
| gpt-oss-120b | 0.09 · — · 0.36 | the no-cache control (already works) |

## Step B · Pilot: Gemini 3.6 Flash + gpt-oss-120b

2 tasks × 4 policies × 2 models = **16 runs**, $40 hard cap, $5 per run. The
stop point is unchanged: real per-run costs and first results come back to the
user before any full study. Two regimes run live: implicit cache at 90% off, and
no cache. The Claude explicit-cache regime is covered only by its contract test.

Changes:
- Add `gemini-3.6-flash` to `research/live_study/models.py` with the rates
  above and the promotion noted.
- Leave `gemini-3.5-flash` in the registry, marked as not used because of throttling.
- Nothing in `src/` changes.

## Files

- `research/live_study/probe.py`: new, the throttle probe
- `research/live_study/models.py`: add the chosen models
- `research/live_study/README.md`: gate G2 updated with probe results, Claude
  marked as skipped by choice

## Verification

- The probe writes `docs/runs/<date>-live-study/probe/results.json` with the
  admit rate and cost (expected under $0.10).
- `pytest -q` stays green.
- A 3-turn smoke run per chosen model reports usage in every category before the pilot.

---

# ▶ STEP 6 · EXECUTION PLAN — a live, model-agnostic study at real cost

## Context

Steps 1–5 (notebook below) produced a formal model: session cost is quadratic
without eviction or placement; below a threshold ℓ\* = ((w−r)/r)·Δ, deciding
*where* work runs at insertion beats evicting it later; and cache-blind clearing
loses money on long sessions. None of it has been tested on real agents.

The user's direction for the test is explicit and correct:
- **Live agent runs, not simulation** — real outputs, real thinking tokens, real
  task outcomes. Only live runs can show whether eviction or placement hurts
  accuracy, which no simulation could.
- **Real cost** — from provider-reported usage priced at the platform's actual
  rates, reconciled against the actual bill.
- **Model-agnostic** — three model families with three different caching
  regimes, one design.

Decided: **our harness**, with tools executing inside real task containers, so
the policy is the only variable across arms. Models: **Claude Sonnet 5**,
**Gemini 3.5 Flash**, **gpt-oss-120b** — all through the user's Google Agent
Platform key.

| Model | Caching regime | Endpoint |
|---|---|---|
| Claude Sonnet 5 | explicit breakpoints, write premium (1.25×), reads 0.1×, 3-way usage reporting | Vertex, native Anthropic Messages API (`AnthropicVertex`, region `global`) |
| Gemini 3.5 Flash | implicit, no write premium; cache detail often unreported | Vertex OpenAI-compatible (working today) |
| gpt-oss-120b | implicit, 90% off cached input | Vertex OpenAI-compatible, same key |

## Stage 0 · Gates — nothing is spent until these pass

| Gate | Check | If it fails |
|---|---|---|
| **G1 Containers** | Docker Desktop running; pull one SWE-rebench image; run its tests; **time them under x86 emulation on the M2** | move execution to a native x86 VM in the user's GCP project |
| **G2 Model access** | one ~10-token request each to Sonnet 5, Gemini 3.5 Flash, gpt-oss-120b with the existing key | Claude: install `gcloud` and use ADC (user step); if still blocked, run Gemini + gpt-oss and say so |
| **G3 Real prices** | the actual Vertex rates for all three models — input, cache read, cache write, output — recorded with date and source URL | no model runs without a sourced rate |
| **G4 Reconciliation** | whether Cloud Billing export (or the billing console) can show the experiment window | per-call usage stays the record, and the report says the bill was not reconciled |

Environment facts found in planning: M2 arm64, 16 GB RAM, 117 GB free; Docker
installed but not running; `gcloud` not installed. SWE-rebench images are x86 —
expect emulation to be slow, and at most two concurrent containers.

## Stage 1 · Real cost, model-agnostic (library)

The principle, from §3 of the vision: **backends normalize provider usage into
one resource vector; nothing above the backend sees a provider name.**

- **Usage gains a cache-write category.** Today `runtime/telemetry.py::normalize`
  splits `uncached / cached / output`. Add `cache_write`. Claude reports it
  directly (`cache_creation_input_tokens`); the implicit-cache providers report
  zero.
- **`Pricing` gains `cache_write_per_million`**, and `charge()` bills all four
  categories. Rates carry a revision and source, as they already do.
- **Claude backend** in `runtime/backend.py`, implementing the existing
  `ModelBackend` protocol: `AnthropicVertex`, explicit `cache_control` on the
  static prefix plus automatic caching for the growing tail, and a translator
  between our OpenAI-shaped messages/tools and the Messages API.
- **gpt-oss** needs configuration only — it uses `CompatibleBackend`.
- **Contract tests** per backend, against recorded real responses through a mock
  transport, in the style of `test_live_backend_contract_without_network`.
- **Model-agnostic check:** no provider name appears anywhere under `planning/`.

## Stage 2 · Real tasks in real containers

- **Workload: SWE-rebench**, which publishes pre-built Docker images and an
  evaluation harness. Real repositories, real issues, and a real verifier — the
  task's own failing tests must pass.
- **Task selection** (reading the trajectory dataset only to choose tasks): an
  image exists; the reference OpenHands run was **long (≥ 50 turns) and
  resolved** — long enough to reach the regime the model is about, and known to
  be solvable; Python; moderate image size.
- **A container-backed coding agent** in `src/agents/`: read, search, patch,
  command and test execute through `docker exec` in the task's container, with
  the same containment and timeouts as `BaseAgent`. Verification runs the task's
  FAIL_TO_PASS / PASS_TO_PASS tests.

## Stage 3 · The four policies — the experiment's only variable

| Policy | What it does | Stands in for |
|---|---|---|
| **P0 append-only** | never evicts (current `react`) | the naive baseline |
| **P1 threshold compaction** | at a token threshold, keep the first K and last J messages and summarize the middle with a **billed** LLM call | OpenHands condenser, Claude Code auto-compact |
| **P2 oldest-first clearing** | at a threshold, replace older tool outputs with `[cleared]`, keep the 3 newest | LangChain `ClearToolUsesEdit` |
| **P3 EconoContext** | the Step 4 policy: **place at insertion** (root, subagent or pointer) by forecast root lifetime against L\*; evict retroactively only above ℓ\* or on a cold cache; batch to the earliest break | ours |

- P1 and P2 need an **assembler hook** that transforms history before rendering;
  today the assembler is append-only by design.
- P3's forecast of remaining root lifetime: from the agent's own plan when one
  exists, otherwise a stated prior — the first version is deliberately simple,
  and its accuracy is itself measured.
- All thresholds are set once, before any run, and identical across models.

## Stage 4 · Pilot — then decide the full study on real numbers

**2 tasks × 4 policies × 3 models = 24 runs.** Two purposes: shake out bugs, and
**measure real cost per run per model** — so the full study's budget comes from
real numbers rather than estimates.

- Rough pre-pilot estimate, per ~64-turn run: Sonnet 5 ~$1.70, Gemini 3.5 Flash
  ~$1.50, gpt-oss-120b ~$0.30 (first-party list prices; Vertex rates from G3
  replace these).
- **Pilot hard cap: $40**, checked before every run.
- **Stop point:** the pilot's real per-run costs and first results come back to
  the user. The full study needs a separate approval.

## Stage 5 · Full study (separate approval)

Proposed **15 tasks × 4 policies × 3 models = 180 runs**, budget = the pilot's
real per-run cost × 180, with a hard cap. Reported per model and policy:

- **success rate and total cost together**, including failed runs; cost per
  *successful* task, with its definition
- tokens by category, cache hit rate, latency
- the model's predictions checked on real runs: does P3 beat P2 where the
  provider ratio says it should (R5)? does delegation pay only past L\* (R7)?
- **paired comparisons** — the same task across policies — since 15 tasks gives
  wide intervals

## Stage 6 · Reconcile against the bill

Per-call usage × G3 rates, summed per run, compared with the billing record for
the experiment window. Gemini matters most here: its cache detail was reported on
only 14% of our earlier calls, so its per-call figure is an upper bound and only
the bill says what it really cost.

## Files

- `src/econocontext/runtime/backend.py` — Claude backend
- `src/econocontext/runtime/telemetry.py`, `config.py` — cache-write category and rate
- `src/econocontext/assembler.py` — history-transform hook for P1/P2
- `src/econocontext/planning/` — P3 placement and eviction
- `src/agents/swe/` — container-backed coding agent and SWE-rebench fixtures
- `research/live_study/` — runner, task selection, budget guard, reconciliation
- `docs/runs/<date>-live-study/` — results, including where the model was wrong

## Honest limits

- **One run per cell is noisy.** Model sampling varies; small differences will
  not be significant, and the report will say which are.
- **Emulation on the M2** may slow or destabilize some test suites (G1 decides).
- **Different models solve different tasks.** Cost is compared at matched
  quality; a cheaper arm that failed is not cheaper.
- **P3 is new research code.** If it underperforms, that is a result, recorded
  as one.

## Verification

1. `pytest -q` stays green, and new contract tests pass for every backend.
2. `grep -ri "anthropic\|gemini\|openai" src/econocontext/planning` returns
   nothing — model-agnostic by construction.
3. A 3-turn smoke run per model, before the pilot, shows usage in every category
   and a cost priced at G3 rates.
4. The budget guard stops a run that would cross the cap, tested with a
   deliberately tiny cap.
5. The results README reports each prediction as held, partly held or refuted,
   with the numbers.

---

# Brainstorm notebook (Steps 1–5)

Working notes for a step-by-step design conversation. Not an implementation
plan yet: the goal of this phase is to understand the problem well enough that a
breakthrough design, if there is one, becomes visible.

---

## Step 1 · Where does an agent's money actually go, and who manages it?

### How production harnesses manage context today (verified 2026-09-25)

| Harness | Trigger | Action | Cost-aware? |
|---|---|---|---|
| Claude Code | token threshold (auto-compact window, configurable) | summarize old messages, keep system/tools/CLAUDE.md prefix | no |
| OpenHands SDK | event/token threshold (`LLMSummarizingCondenser`, e.g. 24K, keep first 2) | LLM summary of older events | no |
| LangChain `SummarizationMiddleware` | fraction / tokens / messages | summarize older, keep recent + tool pairs | no |
| LangChain `ClearToolUsesEdit` | **100K tokens** | replace older tool outputs with `[cleared]`, **keep 3 most recent** | **no** — docs say token management only |
| Deep Agents | threshold | summarize + offload evicted history to a backend | no |
| Subagents (Claude Code, Deep Agents, OpenHands) | the **model** decides | noisy work in a separate window, return a conclusion | no |

**Every one of them is token-triggered and cache-blind.**

Provider mechanics that make this matter (Anthropic): cache **read 0.1×**, cache
**write 1.25×** (5-min TTL) or 2× (1-hour). Claude Code's TTL silently dropped
from 1h to 5m around March 2026 — a live example of the cache economics changing
under users. Adding a single MCP tool changes the prefix and invalidates the
whole conversation's cache.

### The finding: eviction cost depends on *where* and *when*

A policy that frees 20K tokens from a 100K context (5-min Anthropic rates):

| Where the cleared content sat | One-time cost | Saves/turn | Break-even |
|---|---|---|---|
| **Oldest** (k=10K) — what `ClearToolUsesEdit` does | 78,500 | 2,000 | **39 more turns** |
| Middle (k=40K) | 44,000 | 2,000 | 22 turns |
| Newest (k=80K) | −2,000 | 2,000 | immediate |
| **Any position, cache already expired** | **−25,000** | — | **profitable immediately** |

Clearing breaks the prefix at position *k*; everything after *k* must be
re-written at 1.25×. The default policy keeps the *newest* outputs and clears the
*oldest* — the most expensive possible positions. There is a real reason
(old outputs are less relevant), which is exactly the point: **relevance says
clear old, the cache says clear new, and nobody prices the tension.**

And a TTL expiry is a free rebuild — the "reorder is only correct when you were
rebuilding anyway" line from our own vision page, which turns out to be a
concrete, schedulable event.

### What our own measurements already said

- **Delegation overhead lost** on a task that fits: econocontext +9.6% at matched
  quality, no context cap.
- **13 of 15 planning passes had one candidate.** The worker/mode optimizer mostly
  had nothing to choose between.
- **Tool output dominates growth** (~1,578 tokens/turn vs ~108 model output).
- **Reasoning is 71% of billed output.**
- Our estimator caps cache credit at **10%**. Real agents are engineered for
  80–95% hit rates. **In a high-hit regime, cost is dominated by cache-breakage
  events, not by token counts — and we have been modelling the wrong regime.**

### The honest reading

EconoContext's current thesis bets on the decision production agents already
handle tolerably (delegation — the model does it, and subagents work), and
largely ignores the one they all handle badly: **what to evict, where, and when,
given that the provider bills you for re-sending everything after the edit.**

### Candidate reframe (for discussion, not decided)

> The context window is a cache the provider bills per turn, and every agent
> runs it with no eviction policy — just a flush when it overflows.

What falls out of that framing:
- **Position-dependent eviction cost** — genuinely unlike classic caching
  (Belady/LRU assume uniform eviction cost). Evicting at *k* invalidates
  everything after *k*. Possibly a real online-algorithms problem.
- **Schedule evictions onto free rebuilds** — TTL expiry, tool-set changes, a
  compaction that was going to happen anyway.
- **Delegation becomes one eviction strategy among several** — send noisy work to
  a subagent so it never enters the prefix at all, rather than evicting it later.
- **Plugs into existing extension points** — LangChain middleware, OpenHands
  condenser, Claude Code hooks — as a drop-in replacement for the threshold
  policy, without changing how developers write agents.

### Directions this opens

- **A · Cache-aware context lifecycle** — the reframe above. Strongest verified
  gap, universal to long sessions.
- **B · Cross-session evidence memory** — agents re-read the same repo every
  session; our content-addressed, versioned evidence makes cross-session reuse
  *sound*. Strong adoption story.
- **C · Plan-as-forecast** — the hardest input to any cost model is future need,
  and agents already write it down (todo lists, plans). Use it instead of
  hand-weighted recency.
- **D · Keep the joint worker/evidence/mode thesis** — continuity, but our data
  and the crowded literature argue against it as the headline.

A, B and C compose: A is the spine, C makes its forecasts better, B extends it
across sessions.

### Open questions to test before believing any of this

- Does clearing stale context *help* accuracy (less distraction) enough to offset
  its cost? Needs matched-quality measurement.
- Provider cache behaviour is opaque and changes (the March TTL cut). A policy
  tuned to one provider's rates must learn them, not hardcode them.
- Implicit-cache providers (Gemini, OpenAI) have no write premium — break-even
  shifts, but position-dependence remains.
- Is there prior art on position-dependent eviction cost? *Context Compaction
  Theory* (2608.01326) formalizes *what* to keep but, per its abstract, not
  when, not position, not cache. ClawVM ignores cache (verified earlier).
  Needs a proper search before any novelty claim.

---

## Step 2 · Direction A, research-first — and does it include subagents?

**Your question:** should EconoContext extend its planner to subagents, or not
create agents at all and only manage context? Does context-only fit a harness?

### Correction first: cache-aware eviction is already taken

Checked before building on step 1. The single-context version is **prior art**:

| Paper | What it does | Contexts | Position cascade | TTL | Versioned evidence | Delegation |
|---|---|---|---|---|---|---|
| **TokenPilot** (2606.17016) | billing-rate cost model; eviction batched every 3 turns; prefix stabilization at ingestion; **56–87% cost reduction** | **one** | **no** — "single-pass structural purge", segments treated independently | no | no | **no** |
| **CAPC** (2607.15516) | two-tier cost model on Anthropic; query-agnostic compression keeps prefix valid; 49–52% savings | **one** | implicit only | no | no | **no** |
| **Beyond Compaction / CWL** (2606.11213) | typed, dependency-linked episodes; deterministic priority eviction | **one** | not in abstract | no | no | **no** |

Also measured elsewhere: *"shrinking tool output by 38.4% increased billed costs
by 6.8%"* — the step-1 effect, already observed. **Step 1's finding is not our
novelty.** Anything built on "cache-aware eviction" alone falls over on first
review.

### What every one of them shares: one context

All three manage **a single agent's window**. None decides where work runs. None
delegates. That is precisely the layer you asked about — and it is the
unoccupied part.

### The unifying idea: delegation is pre-emptive eviction

Consider noisy work — reading 40 files to find one function.

- **Inline:** the material enters the root's long-lived prefix. It is re-billed on
  every remaining turn, and evicting it later is expensive, because old content
  sits early and early is where the cascade is largest.
- **Delegated:** the material enters a **short-lived** subagent. It is billed only
  for that subagent's few turns and is evicted **for free when the subagent
  exits**. Only a small finding enters the root.

So placement at insertion time decides **which eviction regime the content will
live under**. Eviction is retroactive placement; placement is pre-emptive
eviction. They are one decision at two moments, and they share one cost model.

### Answering your question directly

- **Yes, the planner extends to subagents — and that is where the novelty is.**
  Single-context cache management is done. Multi-context placement plus eviction,
  priced together, is not.
- **No, EconoContext should not create agents for users** — not in the authoring
  sense. Roles, prompts, tools and behaviour stay with the developer and the
  framework. EconoContext decides **placement**: which context an operation's
  tokens live in.
- **A subagent, from this view, is a short-lived address space** — a placement
  target whose contents are freed at exit. That is a memory-management decision,
  not agent authoring, and it fits a harness the way a memory manager fits an
  operating system. TokenPilot is a page-replacement policy for one process;
  EconoContext would be the memory manager across processes.

### It explains our own negative result

Illustrative model (Anthropic 5-min rates; 8K of noisy material; 300-token
finding; 40K root; 3 child calls):

| Root turns remaining | Inline | Delegate | Better |
|---|---|---|---|
| 5 | 14,000 | 18,875 | inline |
| 10 | 18,000 | 19,025 | inline |
| 15 | 22,000 | 19,175 | delegate |
| 30 | 34,000 | 19,625 | delegate |

**Delegation pays only once the root lives ~12 more turns.** Our ledger run
delegated partway through a ~20-call task — inside the inline-wins regime. The
+9.6% is the model's prediction, not a contradiction of it.

It also absorbs **direction C**: the break-even hinges on *remaining root
lifetime*, which is a forecast — and the agent's own plan or todo list is the
cheapest source of it. C becomes an input to A rather than a separate direction.

### Draft problem statement (research-first)

**Multi-context prefix-cache placement and eviction.**

- Content segments arrive online, with sizes and unknown future reference
  patterns.
- Several contexts coexist, each a **prefix-ordered** sequence with its own
  remaining lifetime: a long-lived root, warm existing subagents, and fresh
  short-lived ones.
- **Placement:** assign each segment to a context, or to an external store behind a
  pointer.
- **Holding cost:** size × read rate, per turn, in every live context holding it.
- **Eviction at position k:** cascading cost (length − k) × (write − read); zero
  at context exit.
- **Free-rebuild events** — TTL expiry, tool-set change — make eviction
  temporarily costless.
- **Access under a fidelity constraint:** when a context needs content held
  elsewhere, it can *ask* the holder (cheap, lossy) or *re-read* it (expensive,
  exact), subject to the fidelity the operation requires.
- **Objective:** minimise total billed cost subject to required fidelity at each
  access.

Why it might be genuinely new: it combines **weighted online caching**
(GreedyDual — non-uniform fetch cost), **prefix structure** (SGLang's radix tree
evicts leaf-first for the same cascade reason — but server-side, over GPU memory,
invisible to the bill), and **placement across multiple caches of different
lifetimes**, under a **fidelity constraint on access**. The harness-side,
billed-dollars, multi-context version does not appear in anything checked so far.

### Still open before any novelty claim

- A deliberate search for multi-context or delegation-aware cache work — the
  serving side (Leyline, "Workload-Aware Caching for Multi-Agent Systems",
  "Learning Agent Execution for KV-Cache Management") needs reading, since
  multi-agent KV reuse is adjacent.
- Whether the problem admits a clean online result (competitive ratio, regret),
  or is empirical only.
- Whether TokenPilot's single-context policy is simply the right *inner* policy,
  with EconoContext contributing the placement layer above it — a composition
  rather than a competition.

---

## Step 3 · Is the multi-context angle actually open?

### Everything checked, by what it decides

| Work | Decides placement? | How | Prices billed cache? | Contexts | Layer |
|---|---|---|---|---|---|
| TokenPilot | no | billing cost model | **yes** | one | harness |
| CAPC | no | cost model | **yes** | one | harness |
| Beyond Compaction / CWL | no | priority policy | no | one | harness |
| Leyline | no | policy directives | no — GPU/latency | one | **server** |
| Workload-Aware Caching (MAS) | no | recompute/DAG score | no — latency | result cache | harness |
| CacheScout | no | learned execution model | no — GPU/TTFT | multi-agent KV | **server** |
| Cost-Utility Alignment | no | offline diagnosis | no | — | analysis |
| **Context-Folding** | **yes** — branch/fold | **RL-trained** | no | branches | model |
| **SearchSwarm** | **yes** — delegation | **SFT-trained** | no — quality | many | model |
| **Claude Code / Deep Agents subagents** | **yes** | **model judgment** | no | many | harness |

### Verdict: two populated sets, an empty intersection

- **Set 1 — prices cache-aware context decisions:** TokenPilot, CAPC. Always **one
  context**.
- **Set 2 — decides placement / delegation:** Context-Folding, SearchSwarm,
  production subagents. Always decided by **the model** (RL-trained, SFT-trained
  or prompted judgment), **never by an explicit billing-aware cost model**.
- **Nothing found in the intersection:** placement priced against the provider's
  prefix-cache billing, across a long-lived root and short-lived subagents.

**Plausibly open, not proven.** About fifteen papers read at abstract/summary
level; SearchSwarm's full text (which may contain cost analysis) was not read. A
systematic full-text search is required before any paper claim.

### Two nuances that sharpen the scope

**Leyline makes the cascade a hosted-API phenomenon.** It fixes position shifts
server-side with RoPE-rotation correction, so on self-hosted inference, moving
content need not invalidate the cache. The (length − k) cascade is a property of
**exact-prefix-match billing**, not of transformers. So:
- EconoContext's cascade pricing targets **hosted APIs** — which is what most
  developers actually use.
- The cascade coefficient is a deployment parameter in the rate vector: near
  zero on a backend with position correction. The policy should behave
  differently on each — a clean, testable property.

**Context-Folding is the formidable baseline.** Learned branch/fold reaches an
active context ~10× smaller than ReAct. It is the *learned* version of
delegation-as-pre-emptive-eviction. Our distinction must be precise: **priced
rather than learned, works on any model with no training, cache-aware rather than
cache-blind.** Per the handoff doc, compare a fixed-model mechanism adaptation
separately from its published trained results.

### The candidate claim

> Placement and eviction are one decision about which cache a token lives in.
> Pricing them jointly against the provider's prefix-cache billing — across a
> long-lived root and short-lived subagents — beats both single-context cache
> management and learned or judgment-based delegation, without training the
> model.

With a **falsifiable, quantitative prediction** that no baseline makes:
delegation's value crosses zero as a function of the root's remaining lifetime
(~12 turns under illustrative Anthropic rates).

### Where this may be heading: a memory hierarchy for agent state

Every token lives in some cache, each with a lifetime and a billing model:

| Level | What it is | Access | Eviction |
|---|---|---|---|
| **L1** | root context, warm prefix | cheapest per read, but re-billed every turn | **position-dependent** — early is expensive |
| **L2** | warm subagents holding content | *ask* — cheap, lossy | free at exit |
| **L3** | external evidence store, behind a pointer | *retrieve* — exact, costs a re-read | cheap |
| **Recompute** | re-run the tool | exact, most expensive | — |

Everything from the earlier design falls into place:
- **delegation** = placement into L2 rather than L1
- **eviction / compaction** = demotion from L1 to L3
- **cross-agent asking** = an L2 access
- **representations** (FULL / COMPRESSED / POINTER) = which level, at which
  fidelity
- **recovery cost** = the access cost from each level
- **TTL expiry** = an L1 flush — a free rebuild to schedule evictions onto
- **versioned evidence** = **cache coherence**. When a file moves v8 → v9, every
  level holding v8 is stale. Our bindings are a coherence protocol — and nothing
  in the table above has one.

That last point may be the most distinctive: the hierarchy is familiar, the
position-dependent L1 eviction and lossy L2 access are unusual, but **coherence
across levels for agent state** does not appear anywhere in the checked work.

### Correction: the hierarchy itself is not ours

Checked immediately after proposing it. **MemGPT** (2310.08560, 2023; now Letta)
introduced exactly this vocabulary — main context as RAM, external context as
disk, recall and archival storage, with the LLM paging between them by function
call. And **"Agentic Context Management"** (2607.21503) already frames memory and
cost as *lifecycle and architecture* problems, with five primitives including
*anticipating* (forecasting need) and "validated compaction achieves linear cost
with preserved fidelity" — on conversational benchmarks, with no placement, no
cache billing and no TTL.

So: **the hierarchy is shared vocabulary, and the lifecycle framing is taken.**
Credit both plainly.

### The honest line: what is theirs, what might be ours

**Not ours — cite plainly:**
- cache-aware eviction within one context — *TokenPilot, CAPC*
- the memory hierarchy and LLM-driven paging — *MemGPT / Letta*
- memory and cost as lifecycle/architecture — *Agentic Context Management*
- delegation or branching to shrink context — *Context-Folding (RL), SearchSwarm
  (SFT), production subagents (judgment)*
- position-dependent invalidation as a phenomenon — *Leyline, fixed server-side*
- learned execution prediction for cache retention — *CacheScout, server-side*

**Still looks open — the narrow, defensible core:**
1. **Placement priced against billed prefix-cache economics** — choosing root vs
   subagent vs store by an explicit cost model rather than model judgment or
   training.
2. **Harness-side cascade pricing for hosted APIs** — evicting at *k* costs
   everything after *k*; TokenPilot treats segments independently, and Leyline
   removes the cascade only on self-hosted kernels.
3. **Scheduling evictions onto free rebuilds** — TTL expiry, tool-set changes.
4. **Coherence across contexts** — version-aware staleness for every level
   holding a superseded file.
5. **The joint decision, and its falsifiable prediction** — delegation's value
   crosses zero as a function of the root's remaining lifetime.

### The positioning, in one line

> **MemGPT gave agents a memory hierarchy. EconoContext puts a price on it.**

It is honest about precedent, it places the contribution exactly where the
saved project principle says it belongs — the pricing model, not a novelty
claim about the architecture — and it tells a reviewer in one sentence what to
compare against.

### One formal handle worth keeping

Without eviction or placement, **session cost grows as O(T²)**: each of T turns
re-sends O(T) accumulated context. Prefix caching cuts the constant ~10× but
leaves it quadratic. Placement and eviction are what change the growth rate —
which gives the formalization a clean target and a reason the effect compounds
on exactly the long sessions developers care about.

---

## Step 4 · The formal model

Built up from billing mechanics; every result checked by turn-by-turn simulation
of exact-prefix billing.

### Billing

A call sends a sequence of length *n*. The provider bills the longest cached
prefix *h* at the read rate and the rest at the write rate:

```
cost = r·h + w·(n − h) + o·m          (m = output tokens)
```

Relative to base input: Anthropic 5-min r=0.10, w=1.25 · 1-hour w=2.0 ·
implicit caches (OpenAI, Gemini) w=1.0, r between 0.10 and 0.50.

### The results

**R1 · The baseline is quadratic.** Append-only, growing Δ per turn, the prefix
is re-read every turn: total ≈ r·Δ·T²/2. **Θ(T²).** Caching cuts the constant
~10×; only placement and eviction change the growth rate.

**R2 · Eviction cost is linear in the tail.** Evicting ℓ tokens at position *k*,
with tail τ = n − ℓ − k after it:

```
one-time cost  = (w − r)·τ − r·ℓ
saving         = r·ℓ per remaining turn
break-even     ≈ ((w − r)/r) · (τ/ℓ)  turns
```

Governed by two numbers: **τ/ℓ** (how much sits after what you free) and the
**provider ratio (w − r)/r**.

**R3 · Offline, eviction is immediate-or-never.** Every turn you wait appends Δ
after the segment, growing τ — so the cascade price rises *and* fewer turns
remain to recoup it. Simulated for segments of 2K–40K: cost of delay is
monotonically increasing in every case.

**R4 · Online, "wait and learn" breaks below a threshold.** The classic ski-rental
rule — keep paying rent until cumulative rent equals the buy price, then buy —
requires rent to outrun the buy price. Here rent is r·ℓ per turn but the buy
price grows by (w − r)·Δ per turn. So:

```
ℓ*  =  ((w − r) / r) · Δ
```

**Below ℓ\*, the rule never fires: "wait and learn" degenerates to "never
evict".** You cannot discover the horizon by observing the session. **A forecast
of remaining lifetime is necessary, and the decision is effectively made at
insertion — which is to say, it is placement.**

**R5 · ℓ\* spans 19× across real pricing** (Δ = our measured 1,578 tokens/turn):

| Pricing | (w−r)/r | ℓ\* |
|---|---|---|
| Anthropic 1-hour | 19.0 | ~30,000 |
| Anthropic 5-min | 11.5 | ~18,100 |
| implicit, 90% off | 9.0 | ~14,200 |
| implicit, 75% off | 3.0 | ~4,700 |
| implicit, 50% off | 1.0 | ~1,600 |

On Anthropic, ℓ\* sits above essentially every tool output — **placement is the
decision that matters.** On a shallow-discount implicit cache, most segments
clear ℓ\* and retroactive eviction works. **The relative value of placement
versus single-context eviction is provider-dependent and predictable from one
ratio** — a falsifiable cross-provider prediction, and the rigorous version of
"compose with TokenPilot".

**R6 · One cascade per break point.** Evicting a set of segments diverges the
prefix at the earliest one. Everything after the break is rewritten anyway, so
**once you pay to break at *k*, evicting anything after *k* is free — in fact
profitable.** Batch, and sweep everything evictable past the break.

**R7 · Cheaper reads make delegation less attractive.** Delegation pays when the
root's remaining lifetime exceeds

```
L*  =  (overhead − w·(S − F)) / (r·(S − F))
```

(S = noisy material, F = returned finding). **L\* ∝ 1/r.** As providers cut
read prices, carrying content in the root gets cheaper and delegation needs a
longer root to pay off. Counterintuitive and falsifiable.

**R8 · Free rebuilds reopen the window.** When the cache is cold (TTL expired),
h = 0 either way and the cascade term vanishes: eviction saves w·ℓ immediately.
So below-ℓ\* segments, never worth evicting in a warm context, **should be swept
at every cold-cache moment.**

**R9 · Edits are broadcast cascades.** A change to a source invalidates it in
*every* context holding it — one cascade per holder. Placing a frequently edited
file early in a long-lived prefix is expensive; it belongs late, or in a
short-lived context. In a coding agent, the file being edited is the most
volatile thing in the session.

### The policy that falls out

1. **At insertion — place.** Root inline, subagent, or pointer, chosen by
   forecast remaining root lifetime against L\*.
2. **Above ℓ\* — evict on a ski-rental threshold** once cumulative holding cost
   reaches the current cascade price.
3. **Below ℓ\* — never evict retroactively**, except at free rebuilds.
4. **Batch** every eviction to the earliest break point and sweep past it.
5. **Place volatile sources late** or in short-lived contexts.

### The theory it connects to

Below ℓ\*, the problem is **ski rental with predictions** — the canonical example
in *learning-augmented online algorithms* (Purohit, Svitkina & Kumar, NeurIPS
2018: an algorithm trades **consistency**, when the prediction is right, against
**robustness**, when it is wrong). Our variant adds a **buy price that grows
linearly with time**, and **free-purchase events** (TTL expiry).

That gives the paper a real theoretical spine, and it makes direction C rigorous
rather than decorative: **the agent's own plan is the prediction**, and the
consistency–robustness trade-off says exactly how much to trust it.

### Open questions

- Competitive ratio of the growing-price ski rental above ℓ\*, deterministic and
  randomised.
- The consistency–robustness frontier with a plan-derived prediction — and how
  wrong agents' todo lists actually are, empirically.
- Whether multi-context placement admits a bound, or only the per-segment
  decision does.
- ~~Verify the Purohit et al. framing~~ — **verified.** Purohit, Svitkina & Kumar,
  *Improving Online Algorithms via ML Predictions*, NeurIPS 2018: ski rental and
  non-clairvoyant scheduling; algorithms "oblivious to the performance of the
  predictor, improve with better predictions, but do not degrade much if the
  predictions are poor." The optimal consistency–robustness frontier for ski
  rental is in *Optimal Robustness-Consistency Trade-offs for Learning-Augmented
  Online Algorithms* (arXiv 2010.11443). Our growing-price, free-purchase variant
  is not covered by either — that gap is the theory contribution to establish.

---

## Step 5 · Can our own logs test this? No — and what can

**Your question:** are our logs accurate, or do they come from the same
underlying idea as the current vision — making them useless as a stress test?

**Both concerns are real.** Audited the 124 logged live calls:

| Check | Finding | Consequence |
|---|---|---|
| **Accuracy** | provider reported cache detail on **17 / 124 calls (14%)**; the rest charged at the uncached rate as an upper bound | the model's central variable, the cached prefix *h*, is **unknown on 86% of calls** |
| **Length** | 7–21 calls per run | the model's regime — quadratic growth, L\* ≈ 12, eviction windows to turn 47 — needs dozens to hundreds |
| **Evictions** | prompt shrank **once** across all runs | our harness is append-only: **no eviction data at all** to test R2–R8 |
| **Provider** | Gemini, implicit caching, **no write premium** (w = 1) | cannot test the Anthropic cascade (w = 1.25, ℓ\* ≈ 18K) — the headline regime |
| **Circularity** | Δ = 1,578 was measured on runs where *our own policy* made placement choices | deriving ℓ\* from Δ and validating ℓ\* on the same runs would be circular |

**Verdict: the logs are accurate at what they record — real provider token counts
— but they are not an independent test of this model.** They come from the old
vision's append-only harness, on a provider without a write premium, in sessions
too short, with cache detail missing on 86% of calls.

### The one thing they legitimately teach

**Growth is bursty, not constant.** Coefficient of variation 0.39 to **1.76**; one
run swung from 115 to 12,517 tokens in a turn. That is about agent behaviour, not
our policy — so it is a fair finding, and it breaks R4's constant-Δ assumption.

And a burst *is* a large segment — one big tool read. So the model should be
restated around a **stream of segment arrivals of varying size**, not a steady
Δ. ℓ\* still holds on average, but the variance matters for online decisions,
and the bursts are precisely the segments placement cares about most.

### What to use instead

**Independent workload: public agent trajectories** — long, real, multi-turn,
generated by other people's harnesses, so no circularity:

| Dataset | Size | Harness |
|---|---|---|
| nvidia/SWE-Zero-openhands-trajectories | 318K | OpenHands |
| nvidia/Open-SWE-Traces | 200K+ | SWE-agent + OpenHands |
| nebius/SWE-agent-trajectories | 80K | SWE-agent |
| nvidia/SWE-Hero-openhands-trajectories | 34K | OpenHands |
| nebius/SWE-rebench-openhands-trajectories | — | OpenHands |

**Deterministic billing: Anthropic explicit caching.** Every response splits input
three ways — `cache_read_input_tokens`, `cache_creation_input_tokens`,
`input_tokens` — so *h* and the write are fully reported. That fixes the 14%
problem outright.

### The methodology: cheap for cost, live only for quality

1. **Trace-driven simulation for cost.** Replay public trajectories through the
   exact-prefix billing model under every policy — append-only, threshold
   compaction, `ClearToolUsesEdit`, TokenPilot-style, ours. Millions of policy
   evaluations, zero API spend, no circularity.
2. **Calibrate on a small live Anthropic sample.** Confirm the simulator's bill
   matches Anthropic's three-way reported usage. That validates the billing model
   itself.
3. **Live runs only for quality.** Trace replay is valid for cost but **not for
   quality**: evicting content changes what the agent does next, and a replayed
   trace assumes it would not have. So simulation gives cost under unchanged
   behaviour; live runs measure whether eviction or placement hurts accuracy.

### What the traces measure that nothing else can, at scale

- the **segment-size distribution** — what fraction of real tool outputs fall
  below ℓ\*, i.e. how often placement is the only lever
- the **re-reference pattern** — in hindsight, whether each segment was ever used
  again. That is the **ground truth for the forecast** in ski rental with
  predictions, and it lets us measure how good a plan-derived prediction is
- the **offline optimum** — the upper bound on savings, computable exactly with
  hindsight
- the **empirical competitive ratio** — how close each online policy gets to it

That is the stress test the model actually needs, and it can run before a single
paid call.

Step 5 sources: [SWE-Zero trajectories](https://huggingface.co/datasets/nvidia/SWE-Zero-openhands-trajectories),
[Open-SWE-Traces](https://huggingface.co/datasets/nvidia/Open-SWE-Traces),
[SWE-agent trajectories](https://huggingface.co/datasets/nebius/SWE-agent-trajectories),
[SWE-rebench trajectories](https://huggingface.co/datasets/nebius/SWE-rebench-openhands-trajectories),
[Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).

Step 3 sources: [Leyline](https://arxiv.org/abs/2606.01065),
[Workload-Aware Caching for MAS](https://arxiv.org/abs/2607.20495),
[CacheScout](https://arxiv.org/abs/2608.14624),
[SearchSwarm](https://arxiv.org/abs/2606.09730),
[Cost-Utility Alignment](https://arxiv.org/abs/2608.26195),
[Context-Folding](https://arxiv.org/abs/2510.11967).

Step 2 sources: [TokenPilot](https://arxiv.org/abs/2606.17016),
[CAPC](https://arxiv.org/abs/2607.15516),
[Beyond Compaction](https://arxiv.org/abs/2606.11213),
[SGLang radix eviction](https://docs.sglang.io/docs/advanced_features/radix_eviction_policy),
[When Fancy Eviction Fails](https://arxiv.org/abs/2609.28870).

Step 1 sources: [Claude Code context window](https://code.claude.com/docs/en/context-window),
[OpenHands condenser](https://docs.openhands.dev/sdk/guides/context-condenser),
[LangChain built-in middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in),
[Anthropic caching TTL mechanics](https://brandonwie.dev/posts/anthropic-prompt-cache-ttl),
[Claude Code TTL regression](https://github.com/anthropics/claude-code/issues/46829),
[Context Compaction Theory](https://arxiv.org/abs/2608.01326).
