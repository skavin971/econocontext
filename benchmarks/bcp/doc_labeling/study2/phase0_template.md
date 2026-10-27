# Phase 0 · Fixing the measurements of Studies 1–2 and measuring the ceiling (2026-10-27)

> **In one line:** no new agent trials. We regraded every Study 1–2 answer with the BrowseComp-Plus judge prompt,
> priced Jev in dollars, audited every Study 2 trace with one chunker for all arms, measured where answers sit in the
> gold documents, and collected the numbers Phase 1 needs. Aggregates only.

**Why:** an audit of our Study 1–2 reports concluded that the evidence supports a falsification study, not a method
claim, for four reasons:
- page size explains the savings so far;
- Jev's order was never tested online against a free lead-first order;
- Study 2 was graded by contains-gold;
- Jev's cost wasn't counted.

The CLM comparison also swaps the whole agent: ours stops at the budget, CLM's compacts and continues. Phase 0 fixes
the measurements; Phase 1, designed separately, runs on a local Mac (`docs/handoff-local.md`).

## 1. Regraded accuracy

**Judge:** {{JUDGE}}.
- **Prompt:** `prompts.GRADER_TEMPLATE_UPSTREAM` (sha256 `71fb29f5…`), the upstream `evaluate_run.py` prompt verbatim.
- **Sampling:** temperature 0.7, top_p 0.8, top_k 20, 4,096 output tokens, one user message.
- **What gets graded:** every trial with a final answer. Abstentions and trials without an answer stay incorrect.
- **CLM:** graded from `answer.md`, which carries the "Exact Answer:" line.
- **Old numbers:** the legacy template (sha256 `2f79b052…`) is kept only to reproduce them.

**Study 2** (5 questions × 3 repeats):

{{ACC_S2}}

**Study 1** (3 questions; arms as run then, SQLite BM25, 65,536-token window):

{{ACC_S1}}

## 2. Cost: Jev in dollars, the main model in tokens and FLOPs

**How Jev is priced:**
- **Rate:** $0.042 per million billed input tokens. Output is free, state and question tokens cost the same, and there's no caching discount (TypeSafe list price, docs.typesafe.ai/models.md, checked 2026-10-26).
- **Token counts:** Jev's own reported `usage.input_tokens`, never character estimates.

**How the main model is measured:**
- **FLOPs:** prefix-reuse FLOPs (CLM Eq. 9), unchanged.
- **Tokens per trial:**
  - P = all prompt tokens, summed over calls;
  - U = new prompt tokens, not reused from the cache;
  - G = output tokens.
- **No dollar price** is put on the main model.

{{COST}}

## 3. Trace audit (Study 2, every trial)

**How chunks are compared:** every arm's returned text, M0 and CLM included, is mapped onto one uniform chunking of its source:
- a snippet, in 102-token chunks;
- a document's 8,192-token view, in 400-token chunks;
- both cut by Qwen3-0.6B, so chunk hashes compare across arms.

A chunk counts as returned when the event returned at least half of its characters.

**Admitted** means the chunk's text entered the main model's prompt:
- **Our arms:** always the case, since payloads are appended verbatim.
- **CLM:** the part of the output that survived its 60,000-character output cap and pipes.

{{AUDIT}}

**Why CLM's admitted counts are lower than its returned counts:** CLM pipes or filters most of its tool calls (`jq`, `head`, `python`):
- **Searches:** 731 of 961 (76%).
- **Document reads:** 59 of 66 (89%).

So much of what the service returns never reaches its prompt. Its repeats are re-runs of the same or overlapping
searches: 14–17k repeated returns, against 0.3–0.6k for ours.

**What CLM's released agent does with a cut reply** (`baselines/clm/upstream` at `648a48b`, `clm/clm_harness/clm_agent/harness.py`, read only):
- **How it's detected:** `_extract_tool_call` returns `no_tool_call` when the reply has no tool call, names a tool other than `bash`, or has arguments that aren't JSON.
- **What it keeps:** the reply is appended to the history with its tool calls stripped and its content kept, followed by a user message, "(No tool call in your last turn. When ready, issue your next `bash` command to continue the task.)".
- **What it costs:** the turn counts as a step (`step += 1`, logged as `empty_turn`), and the loop continues.

**What our arms do:**
- **When it happens:** a cut reply has no tool call, so the harness raises "return exactly one tool call", counted as an invalid action.
- **What the agent sees:** the reply's content is appended, followed by an error payload.
- **When it ends:** at 3 invalid actions the trial ends as `invalid_action_limit` (`search_pilot.trial`).

## 4. Jev's order vs free orders (offline, every paged unit of Study 2)

{{ORDER}}

**How to read this:**
- **What each column holds:**
  - **Overlap:** the share of Jev's page-1 chunks that a free order would also have put on page 1.
  - **Relevant documents:** "any chunk on p1" counts gold and evidence documents returned by search that had at least one chunk on page 1.
  - **Original order:** a document's chunks in document order, the order the `original` arm served in Study 1.
- **The finding:** lead-first order (every hit's first chunk, then every hit's second, …) matched or beat Jev on every search-page count here, at no cost.
- **What it doesn't show:** it's offline, so it can't say what the agent would have done with a different page 1. Phase 1 has to test that online.

## 5. Headroom: where the answer sits in the gold documents (dev split, offline)

**Method:** the first whole-word occurrence of the normalized gold answer in each gold document's 8,192-token view, as a Qwen3-0.6B token offset.

{{HEADROOM}}

**A proxy only.** The answer string ignores the supporting evidence the question's other clues need, and a short
answer can occur by coincidence. Most gold documents name the answer early, often in the title.

## 6. Inputs for planning Phase 1 (numbers only)

{{PHASE1}}

## 7. Code and data

**Code** (`benchmarks/bcp/doc_labeling/study2/`):

| Module | Task |
|---|---|
| `trials.py` | string grades |
| `order_audit.py` | order audit |
| `regrade.py` | judge regrade |
| `cost.py` | Jev dollars and main-model tokens |
| `trace_audit.py` | trace audit |
| `headroom.py` | headroom |
| `phase0_report.py` | this page |

Tests are in `tests/benchmarks/test_bcp_phase0.py`.

**Private** (`~/econo/bcp/`, never in git):
- judge verdicts (`judge-phase0/`);
- the trace-audit events (`phase0/trace_audit.json`);
- the headroom table (`phase0/headroom.json`).

**Committed:**
- the parity fixtures (`tests/fixtures/parity.json`, `scripts/check_parity.py`);
- the data checksums (`docs/handoff/data-sha256.txt`).
