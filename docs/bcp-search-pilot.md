# Complete-corpus search pilot

This extends the fixed-document experiment without changing the existing agent,
gateway, or measurement implementation from main. The new entry point is
`python -m benchmarks.bcp.doc_labeling.search_pilot`.

## Design

The current default focuses on problem one, QID 741, once per arm. The earlier
six-trial results for 741, 333, and 600 remain historical and unchanged. Other
questions require explicit `--query-ids` selections. The two arms are:

- `full`: a bounded search agent receives whole document-reader observations.
- `ranked`: the same agent receives Jev-ranked pages for every document it opens.

Both arms search all 100,195 documents in the pinned official corpus. A new,
portable SQLite FTS5 BM25 index is shared, not a question-specific subset. It is
not an official Lucene BM25 reproduction. Search returns ten 512-token snippets
unchanged in both arms; document views are capped at 8,192 answering-model tokens
in both arms. This tests document-reader observations, not snippet compression.
Agents have no preselected DocIDs, gold answers, evidence labels, or oracle
sufficiency information. Each arm independently searches and decides what to
open. Search trajectories can therefore differ.

The full arm's prompt explicitly states that its allowed document view arrives
whole, in original order, without a next-page tool. The ranked arm's prompt
explicitly describes topic-interleaved priority and shared pages, and recommends
leaving an unhelpful first page unless the document provides a concrete reason
to expect a particular missing fact or continuation. Tool descriptions match
each arm. Rankings are imperfect: the prompt does not claim that an unhelpful
first page proves the rest of the document is irrelevant. The manifest stores
both actual system prompts and arm-specific tool schemas.

The initial six-trial pilot made zero next_page calls, including malformed
multi-tool responses. Its token increases came from searches and retained
snippets, not repeated page retrieval. Q741 ranked opened page 1 of 2, searched
for the teammate's WWII history, then answered. Q333/Q600 ranked never opened a
document. These observations do not establish a page-abandonment failure.
The clarified prompts apply to future trials; earlier logs/results remain
unchanged and were not rerun. A fresh output directory is required for a new
prompt version.

The ranked agent supplies one to three short topic descriptions directly in each
`get_document` call, with the document ID. There is no separate model call to
generate topics. The tool scores each
roughly 400-token chunk of every newly opened document with Jev, and packs
deduplicated topic-round-robin pages up to roughly 2,000 tokens. `next_page`
retrieves more of a document; `search` and `get_document` let the agent leave an
unhelpful document. The same document/topic set reuses its saved ranking.
Changing topics starts a new ranking at page 1, saved under a topic-set hash;
`next_page` retains the active topic set for that document.

## Topic chunks, shared pages, and importance percentages

For each topic, sort chunks by descending Jev relevance, breaking ties by their
original positions. Take the highest unused chunk for topic 1, then topic 2,
then topic 3, and repeat. A chunk relevant to several topics is included only
once. Pack this single combined sequence into pages whose text plus source
headers fits about 2,000 tokens. These are shared pages, not one page per topic;
under the default 400-token chunks/2,000-token page budget, the first page has
room for the best chunk from all three topics. Every chunk remains accessible.

Each ranked tool response contains `topics`, page number/count, text,
`importance_percent`, `importance_by_topic_percent`,
`next_page_importance_percent`, and `next_page_importance_by_topic_percent`.
Per-topic arrays align with the topic order in the tool arguments. For each
topic, the page percentage is 100 times the highest Jev score of a chunk on
that page; the overall percentage is the maximum across topics. Next-page
percentages use the same rule for the next prebuilt page, without returning
that page's text. They are null when no page remains. This requires no extra
page-level model or Jev call.

These are uncalibrated relevance-score summaries, NOT calibrated probabilities
that the page is useful as a whole or contains the final answer. Maxima do not
assume chunk independence or inflate scores by combining probabilities. They
cannot prove that later pages are irrelevant or guarantee globally descending
page importance under round-robin ordering. Per-topic scores make gaps visible.

For the current focus, reports separately count serialized JSON document-tool
outputs (`get_document` plus `next_page`), including score metadata, and search
tool outputs. Both have saved/reduction columns versus full. These counts are
payload tokens returned by tools, not repeated model input tokens or model-
generated answer tokens. Agent-generated topic arguments are already included
in provider output and subsequent input usage; new runs have zero standalone
topic-generation overhead. Legacy reports retain their original topic overhead.

Both arms use the same model/sampling and 65,536-token window, up to 32 model
calls. Arm order alternates by question. One stochastic trial per cell is a
pilot, not statistically reliable evidence. Provider calls time out after 120
seconds with no hidden gateway retries. Interrupted tasks retain traces and
refuse silent replay. Exact-name matching is evaluation-only and is not the
official independent LLM judge.

## Token-only execution

The local wrapper is `/Users/ttvaroh/Desktop/Code/research/econo/bcp/run_search.sh`.
It loads the existing git-ignored keys, builds the complete index once, and
starts/stops a private gateway. Results live outside Git under
`econo/bcp/full-search/pilot-001/`.

`summary.json` / `summary.csv` include provider input/output totals across every
agent call, tool counts, opened documents, task outcome/exact match, observation
tokens and latency. Comparison columns report input/output savings against full,
with and without topic overhead. Jev requests and its own input/output counters
remain separate: Jev units cannot be added to Qwen units or called dollar costs.
Failed or missing provider usage stays unknown. Positive savings mean less
usage; negative savings mean more. Fractions are 0–1, not percentages. Archived
or replaced attempts are not silently incorporated as a complete bill.

Per-task `calls.jsonl` contains model requests/responses; `tools.jsonl` contains
searches, document reads, and page deliveries. Ranked documents each have
`jev.jsonl` (parsed returned answers and usage), `rankings.json`, and `pages.json`.
Raw benchmark text and traces never belong in the Git repository.

## Prepared FLOP analysis — opt-in, not part of token runs

After the token run, the following command can post-process saved gateway logs
without new inference. It is intentionally **not** executed by default:

```sh
bash /Users/ttvaroh/Desktop/Code/research/econo/bcp/run_search.sh --report-only --measure-flops
```

The implementation reuses `measure.flops.measure_log`: rebuild/validate prompt
IDs against provider IDs, simulate prefix reuse within each run, and apply the
existing Eq. 9 constants, counting generated reasoning in output. It reports
agent and topic-generation FLOPs, their net reduction before Jev, and Jev's
break-even FLOPs budget per scoring request. Invalid/missing IDs or usage prevent
a validated FLOP headline. Simulated FLOPs are not GPU hardware measurements.

Jev architecture constants are not available. Its whole-system FLOPs therefore
remain unknown, rather than being estimated from Qwen token units. A negative
pre-Jev budget already rules out a compute win under this accounting; a positive
budget only establishes the maximum ranking overhead a win could tolerate.
Retrieval/indexing CPU work and evaluation compute are outside this model-FLOP
comparison. PFLOP calculations must be explicitly requested as report-only work.
