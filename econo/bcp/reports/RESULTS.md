# Updated trials: questions 333 and 600

Token-only, three methods each. Total model tokens = provider input + output over the complete task. Tool outputs are serialized returned payloads; document-tool tokens are a subset. Do not add these measures together.

## Q741 (prior comparison)

- Full output: model **17,234**; all tool output **7,995**; document tools **2,779**. Correct: exact match. 3 model calls.
- Original pages: model **52,328**; all tool output **14,324**; document tools **2,803**. Correct: exact match. 5 model calls.
- Ranked pages: model **17,425**; all tool output **7,204**; document tools **2,067**. Correct: exact match. 3 model calls.

Ranked versus full (positive = fewer tokens):

- Model: -191 tokens saved (-1.1%).
- All tool outputs: 791 tokens saved (9.9%).
- Document tools: 712 tokens saved (25.6%).

## Q333 (new trial)

- Full output: model **263,134**; all tool output **48,743**; document tools **8,884**. Correct meaning*. 10 model calls.
- Original pages: model **410,891**; all tool output **59,943**; document tools **3,149**. Failed: context limit. 13 model calls.
- Ranked pages: model **169,211**; all tool output **31,753**; document tools **3,394**. Correct meaning*. 9 model calls.

Ranked versus full (positive = fewer tokens):

- Model: 93,923 tokens saved (35.7%).
- All tool outputs: 16,990 tokens saved (34.9%).
- Document tools: 5,490 tokens saved (61.8%).

## Q600 (new trial)

- Full output: model **382,704**; all tool output **60,107**; document tools **3,201**. Failed: context limit. 12 model calls.
- Original pages: model **34,878**; all tool output **13,244**; document tools **1,742**. Correct: exact match. 4 model calls.
- Ranked pages: model **337,472**; all tool output **62,255**; document tools **0**. Failed: context limit. 12 model calls.

Ranked versus full (positive = fewer tokens):

- Model: 45,232 tokens saved (11.8%).
- All tool outputs: -2,148 tokens saved (-3.6%).
- Document tools: 3,201 tokens saved (100.0%).

## Interpretation

Q333: Both full and ranked answered copper with qualifiers, consistent with the gold answer Copper on manual inspection; strict exact_match remains false. Ranked needed five searches and one opened document versus full's seven searches and two documents. Its lower spending reflects both different retrieval choices and smaller document output. Original used ten searches, read two pages, and exhausted context without answering.

Q600: Original answered the exact gold name after two searches and one document's first page. Ranked exhausted context after eleven searches without opening a document. Full performed ten searches and opened a document only at the end, then exhausted context without an answer. Neither failed trial is a valid successful-task baseline.

Q741: All methods answered correctly; ranked saved document text but slightly increased total model tokens due to its larger instructions/schema. Original needed an additional page and search.

One stochastic trial per method is not a causal estimate. Different search trajectories, context-limit failures, and formatting-sensitive exact matching matter. No independent LLM judge or PFLOP analysis ran. Jev tokens remain separate and are not interchangeable with Qwen units. Both ranking and search use the same complete corpus through SQLite FTS5 BM25, not official Lucene reproduction.

## Files

Open three-question-token-comparison.html directly in a browser; it is standalone and needs no JSX/build step or network connection. The original summary.json, summary.csv, and per-question/arm calls.jsonl and tools.jsonl remain in the ignored ../full-search/questions-333-600-agent-topics-v1 folder. Q741's source summary remains in ../full-search/problem-741-agent-topics-v1. Raw audit files and benchmark text are not included in the GitHub copy.
