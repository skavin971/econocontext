# Brainstorming prompt: smarter context delivery

Saved 2026-10-25 (ledger L8) so that ideas taken from it are traceable to the exact question asked.

````text
# Brainstorm: smarter context delivery for LLM agents using a cheap relevance classifier

## What we are researching
We want LLM agents to see exactly the context they need, without reading massive documents in full,
and without losing accuracy. The question: can a small, cheap classifier decide what the main LLM
reads first, so the main LLM's context (and total compute) shrinks while it still solves the task?

## Our design pattern (what exists today)
- An agent solves deep-research questions (BrowseComp-Plus: a fixed corpus of 100k web documents).
  Its tools: search (returns 10 hits with snippets) and get_document (returns a document).
- Instead of returning a whole document, we:
  1. CHUNK it (chunk size = 0.2 × the unit: 400 tokens for document pages, ~100 for 512-token snippets),
  2. let the agent write 1–3 short TOPIC LABELS for what it needs ("width of the base", "who designed it"),
  3. ask a cheap classifier (Jev, a hosted model that answers many yes/no or multiple-choice questions
     about one input in a single fast request) how relevant each chunk is to each label,
  4. ORDER chunks by relevance and PACK them into ~2,000-token PAGES,
  5. return PAGE 1 plus a pointer to the next page. NOTHING IS DELETED: every chunk stays reachable
     on later pages, so if ranking is wrong the agent can still find the information.
- Two ordering methods we are comparing:
  - Score + Interleave: one independent 0–1 relevance score per (chunk, label); take each label's best
    unused chunk in turn; show the agent an "importance %" (best score on this page and the next).
  - Strict Rank: one multiple-choice question "which chunk is most relevant to these labels?" whose
    probabilities give a strict 1…N order; pages in that order; no scores shown.
- We also test giving the classifier the task's question vs only the agent's labels (to see if this
  generalizes as a task-agnostic "label + document → relevance" tool for any agent or domain).

## What we have measured so far (Qwen3.8-27B agent, 45 trials, 3 questions)
- No accuracy loss vs reading whole documents.
- Per opened document, ranking cut the document text read by ~33% (agent usually stopped at page 1).
- BUT total context did not drop: search results (~5,700 tokens per search, 10 snippets × 512 tokens)
  were the bulk of the context, and how many searches the agent ran (1–11) varied with luck more
  than with our method. The documents in that test were short (~1.5 pages), capping savings.
- Labels-only classification worked as well as giving the classifier the question (on short docs).
- Next: applying the same paging to search snippets, long documents (5–25k tokens), and comparing
  against CLM (an LLM that edits/compacts its own context).

## Constraints and the balance we want
- Never delete information: everything must remain reachable (paging, not pruning).
- Do not trade fewer tokens for MORE agent calls: each extra call re-sends the whole context, so
  extra "next page" or retry calls can cost more than they save. We want the optimal middle.
- The classifier is cheap and fast, but its calls are not free (latency, cost); the main LLM's
  context size and calls are what we most want to reduce.
- Relevance scores are uncalibrated; the classifier can be confidently wrong.
- Must be measurable: peak context, total input tokens, prefix-cache-aware FLOPs, number of calls,
  and judged accuracy.

## What we want from you
Brainstorm 15–25 creative, novel ideas (go beyond standard RAG reranking) for making this context
delivery smarter and more efficient. Cover a range, for example:
chunking (size, boundaries, adaptive or semantic chunks), labelling (who writes labels, when,
how many, refining them), scoring/ranking (question types, joint vs independent, multi-stage),
paging (dynamic page size, stopping when relevance drops, page summaries or "table of contents",
previews of what later pages contain), confidence and calibration (how to tell the agent when
nothing matches, without deleting), search-result delivery, reuse across steps (caching, carrying
labels forward), interaction with prefix caching, and when NOT to classify at all.

For each idea give:
1. Name and a one-sentence mechanism.
2. Why it could reduce context or calls without hurting accuracy.
3. Its main risk or failure mode (including extra calls or overhead it might add).
4. A cheap experiment to test it in our setup (what to change, what to measure, what result
   would count as success).
5. A rough rating: expected impact (low/med/high) and implementation effort (low/med/high).

Then pick the 3 ideas you would test first and say why, and point out any flaws in our current
design or measurement that we should fix before testing more ideas.
````
