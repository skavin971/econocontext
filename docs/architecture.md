# Architecture and typed handoffs

One process owns one active run and one active operation. API handlers enqueue work; CLI uses the same `Manager`. The root pauses while a child executes. All components communicate through Python calls, not internal HTTP services.

```mermaid
flowchart TD
    API[API / CLI] --> M[Manager: lifecycle, pool, budgets, dispatch]
    M --> P[Planner: retrieve state]
    P --> C[Candidate generator]
    C --> E[Cost model]
    E --> O[Optimizer]
    O --> M
    M -->|CONTINUE / FRESH| L[Common root / child loop]
    L --> A[Assembler: every model request]
    A --> B[Measured backend]
    B --> L
    L --> D[Measured domain tools]
    M -->|REUSE| R[Validate stored result]
    R -->|Return pending tool response| L
    S[Memory: evidence, separate contexts, results] --- P
    S --- M
    S --- A
    T[Telemetry: predictions, inputs, attempts, outcomes] --- M
    T --- A
    T --- B
    T --- D
```

## Ownership

`manager.py` owns run/operation state, the worker pool, cancellation, budgets, plan execution, result publication and parent delivery. It coordinates planning and assembly. `agent_loop.py` contains the shared protocol-valid model/tool loop, not a second planner. Per-worker role, permissions, history and budgets determine available actions.

`planner.py` retrieves bounded metadata and coordinates `candidates.py`, `cost_model.py`, and `optimizer.py`. A lightweight check runs at every root prompt construction and returns without querying unless a trigger fired: a tool observation larger than the finding that would replace it, a source change, context pressure, a failure, or an explicit delegation request. Ordinary continuation retains its selected plan. Changed active inputs cause a controlled stop; V1 does not repair contexts.

The worker is never told this happens. A tool request supplies the operation's lookup keys — its name, arguments and the versions it touched. A call is always answered by its own result: a selected plan may deliver a bounded view of that result plus a finding, never a different result and never a larger one. Exit codes, evidence ids and paths are delivered whole at any size; only the one unbounded field per tool is budgeted, and `truncated` says so on the payload's face.

Executing a call and answering it are separate decisions, so a turn's calls are all executed before any is answered — a preview assembled while a sibling call is unanswered is not protocol-valid. The delegated answer is measured against the literal one and falls back to it unless it is genuinely smaller, which is what makes the claim checkable rather than argued.

`assembler.py` renders the system instructions followed by append-only history and ordered selected evidence/assignments. Tools retain their original order. It assembles every actual model request, including retries and ordinary turns. The optional plan argument is `None` for ordinary root work and root integration. Preview assembly checks feasibility without recording an actual model attempt.

`memory.py` stores versions, current bindings, separate worker histories, prior results and record references. `artifacts.py` writes immutable payloads before database publication. SQLite never remains in a transaction across model/tool execution. `telemetry.py` is also usable independently of the planner.

## Interfaces

Persisted Pydantic records are in `contracts.py`; `ExecutionState` and `CandidateMatches` are typed in-process contexts in `state.py`.

| Interface | Contract |
| --- | --- |
| `MemoryStore.find_candidates(Operation, ExecutionState) → CandidateMatches` | Required references, bounded ranked metadata, eligible lookup candidates and binding revision |
| `CandidateGenerator.generate(Operation, ExecutionState, CandidateMatches) → list[CandidatePlan]` | At most five distinct applicable alternatives |
| `CostModel.estimate(Operation, ExecutionState, CandidatePlan) → Estimate` | Operation cost, separate latency, uncertainty, breakdown, sample count, provenance |
| `Optimizer.select(list[CandidatePlan], dict[str, Estimate], Limits) → CandidatePlan` | Cheapest known-feasible option, or `FeasibilityError` with logged rejections |
| `Assembler.assemble(Worker, CandidatePlan \| None, ExecutionState) → Assembled` | Exact request, tool schemas, token estimate, manifest and fingerprint; never changes selection |
| `Manager.execute(plan, operation, state, call_id, preflight=False)` | Validate and dispatch the selection, or controlled failure |
| `DomainAdapter` | Prepare task; prompts/tools; execution; source refresh; verification; artifact export |
| `ModelBackend.complete(request, context) → ModelResponse` | Normalized assistant message, raw provider usage, synthetic label |

Estimation and assembly use the same serialization and token-count function. Metadata evidence sizes are provisional. If final assembly changes input size by more than 10% (minimum 64 tokens), the manager reselects using the measured rendering estimate; at most twelve passes are allowed. Assembly failures exclude that alternative for the decision.

## Execution paths

- **REUSE:** validate a same-run, side-effect-free operation key and every source requirement; check the real parent's projected input; deliver the stored result. There is no child, child prompt, dummy worker or fabricated attempt. The following real root request is assembled and measured as integration.
- **CONTINUE current root:** close the pending request with an inline assignment and remain in the root loop until `complete_operation`. No extra integration call is fabricated.
- **CONTINUE prior child:** append the selected assignment/evidence to that child's compatible saved context. Acknowledge completion before saving its resumable state.
- **FRESH:** create a scoped child after feasibility checks. If the pool is full, archive the idle child with the lowest stable ID. Logical retirement says nothing about server caches.

The focused view contains required evidence plus at most two direct lexical matches within its budget. Broader adds bounded other corpus/source neighbors and includes focused evidence. Required evidence is never removed to fit a budget. An identical broader view is deduplicated.

## Inspect a complete operation

`econocontext run --step` walks one run component by component as it happens, and `econocontext explain RUN_ID` renders a finished one: evidence written to the store, each assembled prompt, every candidate with its estimate and rejection reasons, the child's own prompt, and what the root received back.

At runtime, inspect `/runs/{id}/trace`, `/runs/{id}/metrics`, or `econocontext export`. Reconstruct any actual request with `econocontext reconstruct MANIFEST_HASH`. Reconstructed inputs are exact retained requests; repeated model outputs or provider tokenization are not guaranteed.
