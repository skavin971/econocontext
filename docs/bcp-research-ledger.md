# BCP research ledger

**Research question:** can a cheap classifier (Jev) decide what the main LLM reads first, so the LLM uses
**less context** to solve BrowseComp-Plus (BCP) questions **without losing accuracy**?

One entry per study, decision or finding, newest last. Aggregate numbers only: BCP text, answers and traces
stay private (the benchmark's canary). Each entry: **Question/hypothesis · What ran · Result · Interpretation ·
Next**.

| # | Date | Topic | Status |
|---|---|---|---|
| L1 | 2026-10-24 | Setup and code walkthrough | done |
| L2 | 2026-10-24 | Study 1 design: 3 questions × 4 arms × 3 repeats | done |
| L3 | 2026-10-24 | Measurement fixes found during Study 1 | done |
| L4 | 2026-10-24 | Study 1 results | done |
| L5 | 2026-10-24 | Labels-only arm (Jev without the question) | done |
| L6 | 2026-10-25 | Evidence-document lengths: why Study 1 savings were capped | done |
| L7 | 2026-10-25 | Study 2 design and decisions | in progress |
| L8 | 2026-10-25 | Brainstorming prompt for new ideas | done |
| L9 | 2026-10-25 | Study 2 setup, long-document demo, smoke test | done |
| L10 | 2026-10-25 | Study 2 results: Q741 (8 methods × 3 repeats) | done |

---

## L1 · Setup and code walkthrough · 2026-10-24

- **What:** read the `bcp-doc-labeling` branch. Two pilots: a fixed-document pilot (one question, one known
  document, no search) and a complete-corpus search pilot (`benchmarks/bcp/doc_labeling/search_pilot.py`).
- **Design pattern:** chunk a document (~400 tokens) → the agent writes 1–3 topic labels → Jev scores each
  chunk against each label → order by relevance → pack into ~2,000-token pages → deliver page 1, with next
  page on request. Nothing is deleted.
- **Environment:** cloud session, no Docker needed for the search pilot; corpus (100,195 docs) indexed with
  SQLite FTS5 BM25.

## L2 · Study 1 design · 2026-10-24

- **Hypothesis:** ranked pages let the agent read less of each document, and so use less context, at equal
  accuracy.
- **What ran:** questions 741, 333, 600 × arms `full`, `original` (pages in document order), `ranked` (Jev,
  one request per chunk), `ranked_doc` (Jev, whole document in one request) × 3 repeats, plus 9 labels-only
  trials (L5). Qwen3.8-27B on Purdue, 65,536-token window, 32 calls, SQLite BM25, fresh context per trial.
- **Measures:** peak context (largest prompt), total input tokens, prefix-reuse PFLOPs with simulated cache
  (`measure/`, CLM Eq. 9), searches, document tokens per opened document, gold/evidence document found.

## L3 · Measurement fixes · 2026-10-24

- **Judge timeout:** the judge reused the agents' 120 s gateway limit; it now gets 600 s with retries.
- **FLOP validation:** Purdue drops `additionalProperties` from tool schemas before templating (verified on
  every call), so FLOPs are measured, with unchanged `measure/` code, on exactly what the server processed.
- **Luck check:** agents can answer from a non-gold *evidence* document; the report now checks both.
- **Purdue outages:** the judge model (`qwen3:32b`) was unresponsive all session, so correctness is exact match
  plus "contains gold" (the normalized gold answer appears in the agent's answer), a stand-in for the judge.

## L4 · Study 1 results · 2026-10-24

| Arm | Answered | Peak context (median) | PFLOPs (median) | Doc tokens saved per opened doc | Page 1 enough |
|---|---|---|---|---|---|
| `full` | 7/9 | 26,991 | 1.6 | 0% | – |
| `original` | 6/9 | 34,446 | 2.1 | −1% | 1/8 |
| `ranked` | 7/9 | 26,346 | 1.5 | 24% | 5/8 |
| `ranked_doc` | 7/9 | 32,372 | 1.9 | **33%** | **7/7** |

- **Interpretation:** classification reliably cuts *document* reading (~33%) with no accuracy loss, but total
  context did not drop: each search adds ~5,700 tokens and search counts varied 1–11 within the same arm
  (luck). An earlier single-run "35.7% saving" did not replicate.
- **Next:** classify search snippets too; test long documents (L6).

## L5 · Labels-only arm · 2026-10-24

- **Hypothesis:** Jev does not need the task's question; the agent's labels plus the document are enough
  (a task-agnostic classifier).
- **What ran:** `ranked_doc_labels` (= `ranked_doc` without the question in Jev's request), 9 trials.
- **Result:** same as `ranked_doc`: 7/9 answered, 32% vs 33% document tokens saved, page 1 enough 7/7.
- **Interpretation:** on short documents, labels alone suffice. Retest on long documents.

## L6 · Evidence-document lengths · 2026-10-25

- **Finding:** all Study 1 evidence documents were short (full views 2,448–3,201 tokens, ~1.5 pages), so the
  most ranking could save was about one page (~30%). Long documents are where it could matter: at the
  8,192-token cap, page 1 would be ~80% less than the full view.
- **Pilot split:** questions 1226 and 1215 have long gold documents (5,487–25,141 tokens); 986 and 570 have
  long evidence documents.
- **Note:** a document longer than 8,192 tokens is cut for every method (BCP's reader cap); evidence past the
  cap is out of reach for all.

## L7 · Study 2 design and decisions · 2026-10-25

- **Topic:** does Jev classification of documents *and* search snippets cut the main LLM's context on BCP,
  compared with the BCP baseline and CLM?
- **Arms:**

| ID | Arm | What it is |
|---|---|---|
| M0 | `full` | BCP baseline, no classification |
| M1 | `ranked_doc` | Score + Interleave (SI), documents only, Jev gets the question |
| M2 | `ranked_doc_search` | SI on documents and search snippets, with the question |
| M3 | `ranked_doc_search_labels` | M2, labels only (no question) |
| M4 | `strict_rank_search_labels` | Strict Rank (SR): Jev ranks chunks 1…N; no scores shown; labels only |
| M5 | `strict_rank_search_reasoning` | M4 + the agent's own reasoning (written just before the tool call) |
| C1 | `clm` | CLM zero-shot, as released |
| C2 | `clm_instructed` | CLM + the paper's Appendix E threshold instruction (Y = 16,000) |

- **Protocol (all methods):** the CLM paper's BCP setup, as our earlier CLM run used: official Lucene BM25,
  10 hits × 512-token snippets, 8,192-token documents, 23,560-token budget (prompt limit 21,512), 100 turns,
  4,096 output tokens. Questions 741, 333, 600, 1226, 1215 × 3 repeats = 120 trials.
- **Chunking:** chunk = 0.2 × unit (400 tokens for 2,000-token document pages; ~100 for 512-token snippets);
  sweeps deferred.
- **What the numbers mean:**
  - Jev `noul` = the probability that the yes/no answer is yes; Jev returns no separate confidence for it.
  - `importance_percent` = computed by our code: 100 × the best `noul` on a page; Jev is never asked for page
    scores. An uncalibrated relevance summary, not a probability of containing the answer.
  - SR uses Jev's `choice` probabilities (they sum to 1) to order chunks strictly 1…N.
  - The answer's `confidence` (0–100) = the answering LLM's self-rating, BrowseComp format; not used in metrics.
- **No leaks:** the agent sees only the question and tool outputs; Jev sees labels, chunks, and the question
  only in M1/M2 and reasoning only in M5; gold answers and gold/evidence docids are used only after a trial. A
  test scans every request for gold data.
- **Disclosure:** 1226 and 1215 were chosen *because* their gold documents are long (answer-key knowledge),
  which may favour ranking compared with a random sample.
- **Decisions:** SR topic starvation is observed, not fixed; SR's "does the document contain anything?" check
  is logged only; DeepSWE deferred to a Study 3 feasibility check.
- **Ideas recorded, not run:** relevance floor (one-line notice when nothing matches), per-topic SR,
  sequential top-k, dynamic page size, predicates instead of topics.

## L8 · Brainstorming prompt · 2026-10-25

- The prompt used to brainstorm new classification, paging and labelling ideas is saved in
  [`bcp-brainstorm-prompt.md`](bcp-brainstorm-prompt.md). Ideas taken from it so far: predicates instead of
  topics (recorded, not run) and the agent's reasoning as Jev input (Study 2 arm M5).

## L9 · Study 2 setup, long-document demo, smoke test · 2026-10-25

- **Setup:** official Lucene BM25 service (`search_server.py`, prebuilt index) on port 8790; Docker started in the
  cloud container (CLM's tasks run in it); CLM at the pinned commit; shared gateway for CLM at 12 calls/min, our
  arms at 8 (Purdue allows 20). pyserini first pulled the CUDA build of PyTorch (several GB, no GPU here) and
  nearly filled the disk; replaced with the CPU build.
- **Long-document demo (Jev only, Q1226):** on gold doc 66144 (7,124 tokens, 19 chunks), SI and SR put the same 4
  chunks on page 1 (1,651 tokens, 77% less than the full view); SI page-1 importance 96%, SR "contains anything"
  0.62. One label (a fact not in this document) scored ≤ 0.08 everywhere. On gold doc 18216 (25,141 tokens) the
  answer string appears nowhere; Jev scored everything low (SI importance 23%, SR "contains anything" 0.01): a real
  no-match case.
- **Caveat for long documents:** in 8 of 9 gold documents of Q1226/Q1215 the answer string appears within the first
  ~40 tokens (the page title). Original-order page 1 already "contains the answer"; what ranking can move forward
  is the evidence for the clues. Answer-string matching is a weak evidence label here.
- **Smoke test (Q741, one trial per method, CLM protocol):** all 8 methods ran end to end; every FLOP validation
  passed; the budget gate stopped two trials at the 21,512 limit after CLM's final-turn notice; M5 passed the
  agent's reasoning to Jev; paged search output averaged ~2,200 tokens per search vs ~5,700 unpaged.

| ID | Result | Peak context | PFLOPs | Searches | Search tokens per search |
|---|---|---|---|---|---|
| M0 full | ✓ | 15,124 | 0.9 | 2 | 5,657 |
| M1 ranked_doc | ✗ budget | 24,615 | 1.4 | 4 | 5,732 |
| M2 ranked_doc_search | ✗ budget | 22,982 | 1.4 | 9 | 2,262 |
| M3 labels | ✓ | 6,100 | 0.4 | 1 | 2,231 |
| M4 strict rank | ✓ | 5,873 | 0.4 | 1 | 2,223 |
| M5 strict rank + reasoning | ✓ | 5,729 | 0.35 | 1 | 2,135 |
| C1 clm | ✓ | 17,015 | 1.0 | – | – |
| C2 clm_instructed | ✓ | 21,346 | 9.4 | – | – |

- **Interpretation:** a pipeline check, not evidence (one trial each).
- **Next:** main runs, Q741 first, 3 repeats per method.

## L10 · Study 2 results: Q741 · 2026-10-25

- **What ran:** 8 methods × 3 repeats = 24 trials, CLM-paper protocol, Lucene BM25; none excluded; every FLOP
  validation passed. Median (min–max); "Contains gold" = the normalized gold answer appears in the answer (the LLM
  judge is unavailable on Purdue).

| Q | ID | Method | n | Answered | Exact ✓ | Contains gold ✓ | Peak context | Input tokens | PFLOPs | Calls | Searches | Search tok/search | Doc tok/doc | Next-page calls |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 741 | M0 | full | 3 | 2/3 | 2/3 | 2/3 | 15,524 (15,337–24,700) | 35,884 (32,389–76,627) | 0.9 (0.9–1.4) | 4 (4–6) | 2 (2–4) | 5,830 (5,763–5,836) | 2,779 (2,779–2,779) | 0 (0–0) |
| 741 | M1 | ranked_doc | 3 | 3/3 | 3/3 | 3/3 | 15,133 (9,184–20,851) | 36,666 (17,535–60,975) | 0.9 (0.5–1.2) | 4 (3–5) | 2 (1–3) | 5,723 (5,635–5,738) | 2,061 (2,058–2,061) | 0 (0–0) |
| 741 | M2 | ranked_doc_search | 3 | 3/3 | 3/3 | 3/3 | 6,462 (6,213–8,544) | 18,238 (11,871–20,539) | 0.4 (0.4–0.5) | 4 (3–4) | 1 (1–2) | 2,296 (2,271–2,301) | 2,057 (2,056–2,057) | 0 (0–0) |
| 741 | M3 | ranked_doc_search_labels | 3 | 3/3 | 3/3 | 3/3 | 8,430 (8,411–8,465) | 20,260 (20,166–20,293) | 0.5 (0.5–0.5) | 4 (4–4) | 2 (2–2) | 2,229 (2,227–2,236) | 2,061 (2,058–2,070) | 0 (0–0) |
| 741 | M4 | strict_rank_search_labels | 3 | 3/3 | 3/3 | 3/3 | 5,864 (5,748–19,362) | 11,051 (10,812–93,775) | 0.4 (0.3–1.2) | 3 (3–9) | 1 (1–5) | 2,222 (2,129–3,069) | 1,985 (1,974–1,986) | 0 (0–2) |
| 741 | M5 | strict_rank_search_reasoning | 3 | 3/3 | 3/3 | 3/3 | 8,114 (5,771–10,412) | 19,359 (10,862–29,799) | 0.5 (0.3–0.7) | 4 (3–5) | 2 (1–3) | 2,200 (2,176–2,200) | 1,984 (1,977–1,985) | 0 (0–0) |
| 741 | C1 | clm | 3 | 3/3 | 3/3 | 3/3 | 20,319 (17,167–21,048) | 290,203 (73,691–308,891) | 4.8 (1.8–6.6) | 18 (6–23) | – | – | – | – |
| 741 | C2 | clm_instructed | 3 | 3/3 | 3/3 | 3/3 | 19,902 (17,810–20,773) | 195,528 (96,298–205,178) | 6.2 (2.8–6.8) | 14 (9–16) | – | – | – | – |

- **Result:** every search-paging arm (M2–M5) answered 3/3, with median peak context 5.9k–8.4k vs 15.5k for the
  baseline (M0) and ~20k for CLM; PFLOPs 0.4–0.5 vs 0.9 (M0) and 4.8–6.2 (CLM). Paged search output was ~2,200
  tokens per search vs ~5,800 unpaged (−62%). Documents: ~2,060 tokens per opened document vs 2,779 (−26%).
- **Interpretation:** on this easy question, classifying search results is what cuts context; document ranking
  alone (M1) did not lower peak context. CLM spends far more compute (many calls and context rewrites) for the
  same answers. n = 3 per cell: direction, not proof.
- **Next:** Q333.

## L11 · Study 2 results: Q333 · 2026-10-25

- **What ran:** 8 methods × 3 repeats = 24 trials, same protocol as L10; none excluded; every FLOP validation passed.

| Q | ID | Method | n | Answered | Exact ✓ | Contains gold ✓ | Peak context | Input tokens | PFLOPs | Calls | Searches | Search tok/search | Doc tok/doc | Next-page calls |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 333 | M0 | full | 3 | 0/3 | 0/3 | 0/3 | 24,141 (23,998–24,220) | 74,658 (61,829–74,669) | 1.4 (1.4–1.4) | 6 (5–6) | 4 (4–4) | 5,717 (5,713–5,743) | – | 0 (0–0) |
| 333 | M1 | ranked_doc | 3 | 1/3 | 1/3 | 1/3 | 24,488 (22,595–24,619) | 83,124 (64,251–91,472) | 1.4 (1.3–1.4) | 6 (5–7) | 4 (3–5) | 5,731 (5,722–5,800) | 3,395 (3,395–3,395) | 0 (0–1) |
| 333 | M2 | ranked_doc_search | 3 | 0/3 | 0/3 | 0/3 | 22,995 (22,635–23,389) | 123,471 (122,268–138,581) | 1.5 (1.4–1.7) | 10 (10–11) | 9 (9–9) | 2,260 (2,217–2,285) | – | 0 (0–0) |
| 333 | M3 | ranked_doc_search_labels | 3 | 0/3 | 0/3 | 0/3 | 23,322 (21,614–23,907) | 123,453 (104,016–126,472) | 1.5 (1.4–1.7) | 10 (9–10) | 9 (8–9) | 2,319 (2,278–2,348) | – | 0 (0–0) |
| 333 | M4 | strict_rank_search_labels | 3 | 0/3 | 0/3 | 0/3 | 22,549 (22,106–22,933) | 119,037 (117,228–120,784) | 1.6 (1.5–1.6) | 10 (10–10) | 9 (9–9) | 2,208 (2,170–2,239) | – | 0 (0–0) |
| 333 | M5 | strict_rank_search_reasoning | 3 | 0/3 | 0/3 | 0/3 | 22,104 (22,068–22,112) | 116,690 (116,623–116,797) | 1.5 (1.4–1.5) | 10 (10–10) | 8 (7–9) | 2,200 (2,178–2,821) | 1,801 (1,801–1,801) | 0 (0–2) |
| 333 | C1 | clm | 3 | 3/3 | 0/3 | 0/3 | 22,479 (21,111–31,038) | 751,069 (267,358–1,122,462) | 15.3 (9.1–19.7) | 59 (17–100) | – | – | – | – |
| 333 | C2 | clm_instructed | 3 | 3/3 | 0/3 | 0/3 | 20,204 (19,059–20,384) | 1,029,626 (488,016–1,190,497) | 11.6 (10.4–19.3) | 100 (45–102) | – | – | – | – |

- **Result:** 1 correct trial out of 24. Every M-arm trial hit the 21,512 budget (one final turn) and all but one
  abstained. Our search never returned an evidence document in any M0 or M2–M5 trial; the one success (M1 r3)
  found an evidence document at step 4, opened it and answered. CLM wrote an answer in 6/6 trials, all wrong,
  using 9–20 PFLOPs (≈10× our arms) and up to 100 calls.
- **Paged search:** each search cost ~2,200 tokens vs ~5,700, so M2–M5 ran ~9 searches before the budget vs ~4 for
  M0/M1. More queries, but none of them surfaced the evidence.
- **Interpretation:** on Q333 the bottleneck is retrieval (finding the right query), not reading. When every arm
  stops at the budget, peak context equals the budget and is not a useful comparison; the useful number here is
  how many searches the budget buys (≈2.2× more with paging).
- **Disclosure:** one C1 call was sent at 31,038 prompt tokens (above the limit) during a rollback-retry in CLM's own
  budget code, as released; recorded, not corrected. It did not change the outcome (wrong answer).
- **Next:** Q600.
