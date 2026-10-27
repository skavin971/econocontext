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

**Judge:** **provisional**: Purdue qwen3:32b, an Ollama build (quantization unknown, likely 4-bit), upstream prompt and sampling with thinking off.
- **Prompt:** `prompts.GRADER_TEMPLATE_UPSTREAM` (sha256 `71fb29f5…`), the upstream `evaluate_run.py` prompt verbatim.
- **Sampling:** temperature 0.7, top_p 0.8, top_k 20, 4,096 output tokens, one user message.
- **What gets graded:** every trial with a final answer. Abstentions and trials without an answer stay incorrect.
- **CLM:** graded from `answer.md`, which carries the "Exact Answer:" line.
- **Old numbers:** the legacy template (sha256 `2f79b052…`) is kept only to reproduce them.

**Study 2** (5 questions × 3 repeats):

| ID | Q741 | Q333 | Q600 | Q1226 | Q1215 | **Total judge** | contains-gold | exact | unresolved |
|---|---|---|---|---|---|---|---|---|---|
| M0 | 2 / 2 / 2 of 3 | 0 / 0 / 0 of 3 | 0 / 0 / 0 of 3 | 0 / 0 / 0 of 3 | 3 / 3 / 2 of 3 | **5/15** | 5/15 | 4/15 | 0 |
| M1 | 3 / 3 / 3 of 3 | 1 / 1 / 1 of 3 | 0 / 0 / 0 of 3 | 0 / 0 / 0 of 3 | 3 / 3 / 2 of 3 | **7/15** | 7/15 | 6/15 | 0 |
| M2 | 3 / 3 / 3 of 3 | 0 / 0 / 0 of 3 | 0 / 0 / 0 of 3 | 0 / 0 / 0 of 3 | 2 / 2 / 1 of 3 | **5/15** | 5/15 | 4/15 | 0 |
| M3 | 3 / 3 / 3 of 3 | 0 / 0 / 0 of 3 | 0 / 0 / 0 of 3 | 0 / 0 / 0 of 3 | 3 / 3 / 1 of 3 | **6/15** | 6/15 | 4/15 | 0 |
| M4 | 3 / 3 / 3 of 3 | 0 / 0 / 0 of 3 | 0 / 0 / 0 of 3 | 0 / 0 / 0 of 3 | 1 / 1 / 0 of 3 | **4/15** | 4/15 | 3/15 | 0 |
| M5 | 3 / 3 / 3 of 3 | 0 / 0 / 0 of 3 | 0 / 0 / 0 of 3 | 1 / 1 / 1 of 3 | 1 / 1 / 0 of 3 | **5/15** | 5/15 | 4/15 | 0 |
| C1 | 3 / 3 / 3 of 3 | 0 / 0 / 0 of 3 | 0 / 0 / 0 of 3 | 1 / 1 / 1 of 3 | 2 / 2 / 0 of 3 | **6/15** | 6/15 | 4/15 | 0 |
| C2 | 3 / 3 / 3 of 3 | 0 / 0 / 0 of 3 | 2 / 2 / 2 of 3 | 1 / 1 / 1 of 3 | 0 / 0 / 0 of 3 | **6/15** | 6/15 | 6/15 | 0 |

Cells: judge ✓ / contains-gold ✓ / exact ✓ of n trials. Judge: **provisional**: Purdue qwen3:32b, an Ollama build (quantization unknown, likely 4-bit), upstream prompt and sampling with thinking off. Judge and contains-gold agree on 75 of 75 answered trials (judge ✓ contains ✗: 0; judge ✗ contains ✓: 0).

**Study 1** (3 questions; arms as run then, SQLite BM25, 65,536-token window):

| ID | Q741 | Q333 | Q600 | **Total judge** | contains-gold | exact | unresolved |
|---|---|---|---|---|---|---|---|
| M0 | 3 / 3 / 3 of 3 | 3 / 3 / 1 of 3 | 1 / 1 / 1 of 3 | **7/9** | 7/9 | 5/9 | 0 |
| S1-original | 3 / 3 / 3 of 3 | 2 / 2 / 1 of 3 | 1 / 1 / 1 of 3 | **6/9** | 6/9 | 5/9 | 0 |
| S1-ranked | 3 / 3 / 3 of 3 | 2 / 2 / 1 of 3 | 2 / 1 / 1 of 3 | **7/9** | 6/9 | 5/9 | 0 |
| M1 | 3 / 3 / 3 of 3 | 3 / 3 / 2 of 3 | 1 / 1 / 1 of 3 | **7/9** | 7/9 | 6/9 | 0 |
| S1-ranked_doc_labels | 3 / 3 / 3 of 3 | 3 / 3 / 0 of 3 | 1 / 1 / 1 of 3 | **7/9** | 7/9 | 4/9 | 0 |

Cells: judge ✓ / contains-gold ✓ / exact ✓ of n trials. Judge: **provisional**: Purdue qwen3:32b, an Ollama build (quantization unknown, likely 4-bit), upstream prompt and sampling with thinking off. Judge and contains-gold agree on 33 of 34 answered trials (judge ✓ contains ✗: 1; judge ✗ contains ✓: 0).

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

| ID | trials | Jev requests (median) | Jev $ per trial, median (range) | Jev $ total | main P | main U (new) | main G (output) | main PFLOPs |
|---|---|---|---|---|---|---|---|---|
| M0 | 15 | 0.0 | $0.0000 ($0.0000–$0.0000) | $0.0000 | 61,831 (19,552–79,700) | 23,915 (12,352–24,762) | 2,039 (826–11,252) | 1.38 (0.69–1.90) |
| M1 | 15 | 1.0 | $0.0002 ($0.0000–$0.0008) | $0.0032 | 63,479 (16,884–91,478) | 22,630 (8,804–24,655) | 1,490 (895–5,246) | 1.32 (0.49–1.60) |
| M2 | 15 | 9.0 | $0.0077 ($0.0014–$0.0112) | $0.0969 | 119,079 (11,872–138,592) | 22,551 (5,662–23,464) | 5,469 (1,017–12,599) | 1.42 (0.36–1.88) |
| M3 | 15 | 9.0 | $0.0084 ($0.0015–$0.0099) | $0.0896 | 117,802 (10,996–137,165) | 22,265 (5,696–23,983) | 3,594 (1,278–10,572) | 1.37 (0.36–1.77) |
| M4 | 15 | 9.0 | $0.0032 ($0.0005–$0.0033) | $0.0379 | 120,793 (10,816–147,465) | 21,759 (5,760–23,433) | 5,254 (1,174–11,344) | 1.47 (0.35–1.76) |
| M5 | 15 | 7.0 | $0.0027 ($0.0005–$0.0036) | $0.0364 | 114,914 (10,868–128,012) | 21,695 (5,780–22,444) | 4,684 (1,144–22,127) | 1.41 (0.35–2.18) |
| C1 | 15 | 0.0 | $0.0000 ($0.0000–$0.0000) | $0.0000 | 355,182 (54,901–1,232,653) | 122,402 (18,005–262,969) | 20,899 (2,652–164,158) | 8.04 (1.27–22.72) |
| C2 | 15 | 0.0 | $0.0000 ($0.0000–$0.0000) | $0.0000 | 488,017 (64,832–1,190,499) | 118,747 (25,808–232,247) | 22,167 (4,441–215,172) | 6.91 (1.67–23.02) |

**Study 2 Jev total: $0.2640** (6,285,989 billed input tokens, 409 requests; 0 failed without usage, 0 retries, 0 harness cache hits). Study 1: $0.0062. Response model field: not logged (Jev.ask keeps answers and usage only); jev-latest = jev-1.14.0 throughout.

**Break-even** (Jev $ ÷ main PFLOPs saved vs the paired M0 trial = the $/PFLOP above which Jev pays for itself):

| ID | pairs | saved FLOPs | median $/PFLOP (range) | no saving |
|---|---|---|---|---|
| M1 | 15 | 13 | $0.00086 ($0.00000–$0.00690) | 2 |
| M2 | 15 | 8 | $0.01086 ($0.00145–$0.60298) | 7 |
| M3 | 15 | 9 | $0.00629 ($0.00236–$17.93287) | 6 |
| M4 | 15 | 5 | $0.00406 ($0.00046–$0.04144) | 10 |
| M5 | 15 | 7 | $0.00215 ($0.00083–$0.18324) | 8 |

**Where SI and SR request characters go** (median share of each request's characters):

| Method | Unit | requests | state (chunks, topics, question) | repeated per-question text |
|---|---|---|---|---|
| SI | search | 179 | 23% | 69% |
| SI | document | 24 | 53% | 42% |
| SR | search | 191 | 89% | 6% |
| SR | document | 15 | 94% | 2% |

## 3. Trace audit (Study 2, every trial)

**How chunks are compared:** every arm's returned text, M0 and CLM included, is mapped onto one uniform chunking of its source:
- a snippet, in 102-token chunks;
- a document's 8,192-token view, in 400-token chunks;
- both cut by Qwen3-0.6B, so chunk hashes compare across arms.

A chunk counts as returned when the event returned at least half of its characters.

**Admitted** means the chunk's text entered the main model's prompt:
- **Our arms:** always the case, since payloads are appended verbatim.
- **CLM:** the part of the output that survived its 60,000-character output cap and pipes.

**Repeats within a trial** (pooled per arm; uniform Qwen3-0.6B chunks, 102 snippet / 400 document tokens):

| ID | trials | documents opened | re-opens | chunks returned | repeated returns | chunks admitted | repeated admissions |
|---|---|---|---|---|---|---|---|
| M0 | 15 | 4 | 0 | 2,956 | 543 | 2,956 | 543 |
| M1 | 15 | 8 | 0 | 2,594 | 563 | 2,594 | 563 |
| M2 | 15 | 8 | 0 | 1,680 | 397 | 1,680 | 397 |
| M3 | 15 | 8 | 0 | 1,555 | 268 | 1,555 | 268 |
| M4 | 15 | 8 | 0 | 1,894 | 457 | 1,894 | 457 |
| M5 | 15 | 7 | 0 | 1,713 | 442 | 1,713 | 442 |
| C1 | 15 | 21 | 10 | 25,891 | 14,246 | 5,912 | 1,849 |
| C2 | 15 | 46 | 21 | 31,545 | 16,667 | 7,316 | 2,422 |

**Cut replies** (output cap hit, no valid action):

| ID | trials with one | cut replies | their completion tokens | share of main FLOPs |
|---|---|---|---|---|
| M0 | 1/15 | 1 | 4,096 | 2.9% |
| M1 | 0/15 | 0 | 0 | 0.0% |
| M2 | 4/15 | 4 | 16,384 | 7.0% |
| M3 | 2/15 | 2 | 8,192 | 3.8% |
| M4 | 2/15 | 2 | 8,192 | 3.7% |
| M5 | 3/15 | 5 | 20,480 | 8.0% |
| C1 | 10/15 | 53 | 217,088 | 18.7% |
| C2 | 9/15 | 91 | 372,736 | 24.2% |

**Recognition gap** (incorrect trials under contains-gold; evaluation only):

| ID | incorrect | search returned a gold doc | a gold chunk on the page read | gold doc opened |
|---|---|---|---|---|
| M0 | 10 | 4 | 4 | 0 |
| M1 | 8 | 3 | 3 | 0 |
| M2 | 10 | 2 | 2 | 0 |
| M3 | 9 | 3 | 3 | 0 |
| M4 | 11 | 3 | 3 | 0 |
| M5 | 10 | 1 | 1 | 0 |
| C1 | 9 | 3 | 3 | 0 |
| C2 | 9 | 3 | 2 | 0 |

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

| Method | paged searches | Jev p1 ∩ hit-order p1 (median) | Jev p1 ∩ lead-first p1 (median) | gold/evidence doc × search | any chunk on p1: Jev / hit / lead | answer-string searches: on p1 Jev / hit / lead | opened documents: overlap with original p1 (median) | answer chunk on p1: Jev / original |
|---|---|---|---|---|---|---|---|---|
| M1 | 0 | – | – | 0 | 0 / 0 / 0 | 0 / 0 / 0 of 0 | 50% (8 views) | 7 / 4 of 7 |
| SI (M2, M3) | 179 | 39% | 29% | 25 | 25 / 14 / 25 | 15 / 6 / 16 of 19 | 50% (16 views) | 10 / 4 of 10 |
| SR (M4, M5) | 191 | 53% | 39% | 28 | 28 / 20 / 28 | 13 / 11 / 16 of 17 | 80% (15 views) | 9 / 3 of 9 |

**How to read this:**
- **What each column holds:**
  - **Overlap:** the share of Jev's page-1 chunks that a free order would also have put on page 1.
  - **Relevant documents:** "any chunk on p1" counts gold and evidence documents returned by search that had at least one chunk on page 1.
  - **Original order:** a document's chunks in document order, the order the `original` arm served in Study 1.
- **The finding:** lead-first order (every hit's first chunk, then every hit's second, …) matched or beat Jev on every search-page count here, at no cost.
- **What it doesn't show:** it's offline, so it can't say what the agent would have done with a different page 1. Phase 1 has to test that online.

## 5. Headroom: where the answer sits in the gold documents (dev split, offline)

**Method:** the first whole-word occurrence of the normalized gold answer in each gold document's 8,192-token view, as a Qwen3-0.6B token offset.

| Bucket (first answer-string occurrence, Qwen3-0.6B tokens) | dev questions (earliest over gold docs) | of which pilot | gold documents |
|---|---|---|---|
| ≤ 512 (inside the snippet) | 41 | 8 | 103 |
| 513–2,000 (page 1, original order) | 1 | 0 | 6 |
| 2,001–8,192 (needs paging or ranking) | 2 | 1 | 9 |
| not in the 8,192-token view | 6 | 1 | 27 |
| **total** | **50** | **10** | **145** |

**A proxy only.** The answer string ignores the supporting evidence the question's other clues need, and a short
answer can occur by coincidence. Most gold documents name the answer early, often in the title.

## 6. Inputs for planning Phase 1 (numbers only)

**How often two arms disagree on the same question** (share of all trial pairs, both repeats of each, pooled over the 5 questions; unresolved verdicts skipped):

| | M0 | M1 | M2 | M3 | M4 | M5 | C1 | C2 |
|---|---|---|---|---|---|---|---|---|
| **M0** | – | 0.13 | 0.13 | 0.07 | 0.20 | 0.27 | 0.20 | 0.47 |
| **M1** | 0.13 | – | 0.13 | 0.07 | 0.20 | 0.27 | 0.20 | 0.47 |
| **M2** | 0.13 | 0.13 | – | 0.07 | 0.11 | 0.18 | 0.16 | 0.33 |
| **M3** | 0.07 | 0.07 | 0.07 | – | 0.13 | 0.20 | 0.13 | 0.40 |
| **M4** | 0.20 | 0.20 | 0.11 | 0.13 | – | 0.16 | 0.18 | 0.27 |
| **M5** | 0.27 | 0.27 | 0.18 | 0.20 | 0.16 | – | 0.20 | 0.29 |
| **C1** | 0.20 | 0.20 | 0.16 | 0.13 | 0.18 | 0.20 | – | 0.36 |
| **C2** | 0.47 | 0.47 | 0.33 | 0.40 | 0.27 | 0.29 | 0.36 | – |

**Variation across the 3 repeats** (per arm and question: max ÷ min; then the median over questions):

| ID | PFLOPs max/min | Jev $ max/min | wall time per trial, median (range), s |
|---|---|---|---|
| M0 | 1.14 | – | 53 (19–212) |
| M1 | 1.08 | 1.01 | 44 (21–105) |
| M2 | 1.20 | 1.34 | 123 (25–255) |
| M3 | 1.19 | 1.17 | 113 (26–233) |
| M4 | 1.15 | 1.30 | 127 (24–233) |
| M5 | 1.89 | 2.51 | 119 (22–390) |
| C1 | 2.16 | – | 515 (110–3,043) |
| C2 | 2.14 | – | 638 (133–3,828) |

**The 40 dev questions not in the pilot set** (benchmarks/bcp/split.json): 85, 95, 1254, 642, 239, 262, 716, 836, 410, 895, 971, 702, 809, 984, 93, 1196, 1222, 546, 370, 728, 5, 60, 186, 1231, 228, 520, 496, 970, 1041, 904, 127, 1232, 907, 1108, 1249, 347, 816, 682, 1198, 625.

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
