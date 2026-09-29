# 6. Status and future

## What is real, what is a placeholder

`grep -rn "# PLACEHOLDER:" econocontext omnigent_layer config` lists every placeholder in the code.

| Part | Status |
|---|---|
| Intercepts, engine, fail-open, validation | **Real**, tested |
| Gates and optimizer (feasibility, objectives, tie-break, `why_not`) | **Real**, tested |
| Assembler (zones, manifest) | **Real**, tested |
| Agent DB, versions, write barrier, byte-identical reuse | **Real**, tested, used in live runs |
| Measurement of every model call (tokens by category, cached tokens included) | **Real**, both arms, at the gateway |
| Omnigent layer (gateway, policy, write barrier, container shell) and SWE-bench bench, official grading | **Real**, live-tested on one instance (resolved), observe mode |
| POINTER on Omnigent | Not wired: the policy can replace a result, but there is no place yet where the agent can reopen the full text |
| Sub-agents on our Gemini setup | Blocked: Omnigent 0.15.0 sends inline sub-agents to the Responses API, which Vertex does not serve ([findings](omnigent-findings.md)) |
| **Ledger** (actual cost in NU and USD, total run cost) | **Real**, tested (PR #4). The SWE-bench run on Omnigent: $0.0977 |
| Runtime spans (timing) | **Real** for model calls on Omnigent; tool and dispatch spans not yet recorded there |
| **Jev planner** (`--jev`) | **To build**: empty slot with a spec. See `planner/jev_planner.py` |
| Planner rules (which candidates to propose) | Placeholder: fixed rules |
| Cost model | Placeholder: token lengths only, **ignores caching**, so it predicts about 2× the actual cost |
| Predictions (turns left, `p_need_again`, output size, subagent cost, latency) | Placeholder: fixed numbers from config (see chapter 3) |
| Cache belief | Placeholder: longest shared prefix; logged, not priced |
| Token counting | Placeholder: characters ÷ 4; exact counts come from the provider |
| Retrieval | Placeholder: keyword match only |
| Decision deadline (50 ms) | Measured, not enforced |
| Anthropic and OpenAI usage mappers | Written, not tested live |
| RESUME, FORK, REPAIR | Named only: the host can't do them yet |

## Known gaps

1. **Dollar budgets are not enforced on Omnigent yet.** Costs are known per call, but the gateway's caps (60 model calls per run, 3M input tokens per day) are what stop a paid run.
2. **The patch can include build output:** `git add -A` also picks up files the agent created, such as Sphinx `_build/`. It was graded correctly anyway, but the patch was 625 KB.
3. **Shell fallback is weaker than it looks.** If `git status` fails after a write or shell tool, only the workspace epoch `*` is bumped, and stored `sys_os_read` results (keyed by their path) stay valid. Git worked in every run so far.
4. **Reusable by default.** A tool that is neither a file read nor a side-effecting tool gets an empty read set, so an identical second call to it would count as answerable from the store (logged only on Omnigent). An explicit list of reusable tools would make this safe by construction.
5. **ZONED rarely matters:** harnesses already send requests in a stable order.
6. **Omnigent specifics** (pinned 0.15.0): its compaction calls `/responses/compact`, which the gateway refuses; policies cannot skip a tool; the bench uses private helpers to start sessions. See [the findings](omnigent-findings.md).
7. **Two files are over the ~200-line guideline:** `engine.py` and `types.py`.

## Where this is going

The order below is the intended order of work. Each step keeps the same interfaces:
`select(candidates, context, constraints)`, the four cost terms, and the Agent DB.

1. **Build the ledger.** Exact cost per call and per run, dollar budgets that work, and predicted vs actual in NU. Everything after this is judged with it.
2. **Make the cost model cache-aware.** The planned changes are listed in the header of `pricing/cost_model.py`:
   - price predicted cache hits at the cache-read rate (0.1 NU on Gemini)
   - add the cache-write premium where a provider charges one (Anthropic: 1.25× to 2×)
   - price the damage of changing a prefix: every token after an edit or reorder is re-sent uncached
   - let output cost and latency grow with context length

   This is where "a smaller prompt can cost more" (chapter 1) gets into the numbers.
3. **Better predictions, from data.**
   - **First, Jev** (`--jev`): a decision model that returns calibrated probabilities. It replaces the fixed `p_need_again` guess with a per-result prediction from the full context. Both numbers are logged, so the two can be compared.
   - **Then learned predictors,** trained on the Agent DB's history:
     - which stored content was actually reopened or used later
     - how many turns runs really take
     - how big outputs are
     - how often the cache actually hits (`observed_hit_ratio`)
4. **Switch on approximate operators, one at a time**, after observe-mode data shows they are safe: POINTER first (the biggest saving in the worked example), then RETRIEVE_FROM_STORE and COMMIT_PENDING. Each needs a quality check, and an autopilot run that you approve separately.
5. **More ways to represent content:** the representation ladder FULL → COMPRESSED → STRUCTURED → POINTER. Only FULL and POINTER exist today. Every piece of content records how it can be recovered and what that costs.
6. **More ways to run delegated work.** Today there are FRESH and REUSE. The worker modes still to add need host support:
   - RESUME: continue a subagent that already holds the context
   - REPAIR: refresh only what went stale
   - FORK: inherit the parent's context
7. **The full vision** (`v0/docs/vision*.html`) treats every step as one decision on three linked axes:
   - **where** the work runs: which agent or worker
   - **what** it sees: which versioned state, in which representation
   - **how** it proceeds: reuse, resume, repair, or start fresh

   Each combination is priced as a whole, with `cost = Σ rate × resource`. With a different rate vector, the same planner prices a self-hosted model: GPU time for prefill, decode and memory held by the context, instead of dollars per token.
8. **More harnesses and providers:** Claude Code, Codex and Pi through Omnigent (the gateway needs a Responses and a Messages codec for them), Anthropic and OpenAI usage tested live, and explicit cache breakpoints for providers that need them. The assembler already computes where the breakpoint goes.
9. **Engineering:** enforce the decision deadline, better retrieval (embeddings, recency, version-aware), and patch hygiene.

## How we will know it works

- **Measure it:** a claim of savings needs the ledger, more instances, and repeated runs per arm, because runs at temperature 1.0 vary a lot.
- **Keep quality:** the resolved rate must not drop.
- **Until then:** every result is reported as a pipeline check, with no savings claimed.

Back to [start](README.md) · How to run things: [TESTING.md](TESTING.md)
