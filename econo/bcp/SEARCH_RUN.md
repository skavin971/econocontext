# Problem one: agent-supplied topics and document-tool tokens

```sh
bash econo/bcp/run_search.sh
```

The complete official corpus is pinned to revision
b27b02bc3e45511b8b82a13e6f90ce761df726f6 (100,195 documents).
The first invocation builds a SQLite FTS5 BM25 index. This portable retriever is
not the official Lucene BM25 implementation; it is identical for all arms.
No gold/evidence DocIDs or gold answers are given to any agent or Jev.
The current default runs only problem one (QID 741), once in each arm. Previous
three-question results remain unchanged in full-search/pilot-001. Current
outputs use a fresh directory: full-search/problem-741-agent-topics-v1.

- Full: search the corpus, then receive each opened document in one observation.
- Original: receive roughly 2,000-token pages in source order, without Jev.
- Ranked: the agent supplies 1-3 short topics in get_document's arguments along
  with docid. There is no separate topic-generation call. The document is chunked
  and Jev-ranked against those topics, then returned one mixed-topic page at a
  time. The agent can leave an unhelpful document after page 1.

Ranked responses include current and next-page importance percentages and
per-topic percentages. These summarize the highest Jev chunk/topic scores, not
calibrated likelihoods of finding an answer. Next-page percentages are null when
no page remains. Scores need no extra page-level model call. next_page reuses
the active topics; get_document with different topics starts a new ranking.

Search is top ten 512-token snippets, unchanged in all arms. Document views are
capped at 8,192 Qwen tokens in all arms, matching the existing reader's limit
(but using the measured answering tokenizer, not Qwen3-0.6B). Ranked pages are
about 2,000 tokens including chunk headers; chunks about 400 tokens.
All agents have 32 model calls and the same 65,536-token context window.
Provider calls have a 120-second timeout and no silent gateway retries.
Failed, missing, or invalid usage is never reported as free compute.

## Outputs

`full-search/problem-741-agent-topics-v1/summary.json` and `summary.csv` default to token-only results:

- task completion/exact-name match, calls by tool, documents opened, latency;
- document-tool and search-tool output tokens separately, including JSON score
  metadata, and comparisons against full document observations;
- complete answering input/output usage and per-call gateway logs;
- input/output comparisons across the complete task, with and without topic
  generation overhead; Jev token units are kept separate from Qwen units;
- per-document/topic-set Jev answers/usage and ranked pages under
  `<qid>/ranked/documents/<docid>/<topic-hash>/`;
- exact-name matching against the benchmark gold answer, without an LLM grader.

PFLOP post-processing is prepared but is NOT run by default. When explicitly
requested later, add `--report-only --measure-flops` to the same shell command.
That reuses existing measure/ Eq. 9 code, validates prompt IDs, simulates prefix
cache reuse, and reports agent/topic FLOPs plus Jev's break-even FLOPs budget.

Jev's architecture constants are unavailable: whole-system FLOPs remain null.
Do not label pre-Jev savings as whole-system savings. Retrieval/index CPU work
and independent evaluation work are outside the model-FLOP comparison.
Exact-name match is evaluation-only, not official model-judge grading. Failed
tasks cannot establish successful-task efficiency. Topic overhead is charged
only in legacy runs. New topics are supplied by the agent inside the normal
tool call; their tokens remain included in provider output/input usage. Repeated
opens with the same document/topics reuse saved ranking scores.

Keys stay in the code repository's .env (GENAI_API_KEY, TYPESAFE_API_KEY).
The script starts/stops its own gateway and prevents idle sleep during inference.
No Harbor, Docker, Java, online pricing, or plotting dependency is required.

Completed trials resume from results. Interrupted trials refuse silent replay:
retain them and select a fresh output folder with BCP_SEARCH_OUTPUT. Reports can
be regenerated without inference by adding --report-only to the command.
