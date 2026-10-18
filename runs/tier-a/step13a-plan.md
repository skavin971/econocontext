# Step 13a plan: BrowseComp-Plus on Tier A (2026-10-12)

**Status.** `ace1995` was pushed after the secrets gate (`step13a-0-push.out`). Nothing has been downloaded, installed or built for 13a yet. **Six points need your decision; they are listed at the end.**

## What CLM's files say (and do not say)

**The pin.** It is at `18dc111`. Moving it to `648a48b` (5 commits) changes nothing in `clm/clm_harness`. The changes are:
- a ContextBench adapter (`clm/clm_contextbench`, new);
- context-string tests;
- a suffix-cache fix (`overlay.py`, 3 lines);
- README edits;
- `pyproject.toml`, which adds `clm_contextbench*` to the packages.

The pin can move with no effect on CLM's agent.

**`clm/clm_harness/configs/bcp.yaml`, quoted:**
- `max_steps: 500  # task-step budget; 100, 500 and 2000 were all used`
- `max_tokens: 4096`, `temperature: 0.7`, `top_p: 0.95`
- `observation_max_chars: 60000  # search results and documents arrive whole`
- `context_budget_tokens: 28672  # 32768-token window minus max_tokens`, with `context_budget_reserve_tokens: 2048`
- `task_template: open_problems`, with the comment `# The task instruction carries the full brief (search tools, answer file, format).` The template renders only `{{task}}`.
- `env: CLM_EDIT_GATE: shrink`, plus nudges at 0.25, 0.5 and 0.75, a persistent nudge at 0.9, `max_num_retry_on_limit: 6`, and `finalize_nudge_turns: 3`.

**Search tools.** These reach CLM as mounted "skills" (`skill_dirs`, `clm_harness/utils/skills.py`). The paper's footnote says: "we expose the search tools as in-context skills."

**Not in CLM's repository, and not in Harbor's registry or adapters** (checked: `harbor dataset list`, the hub, and `laude-institute/harbor/adapters`):
- the BrowseComp-Plus task directories;
- the search skill;
- the answer format;
- the judge prompt.

**From the CLM paper, Appendix E (quoted in step 12f):**
- "The search tool returns the top 10 snippets of 512 tokens and the document reader is capped at 8,192 tokens."
- "... up to 4,096 generated tokens per call, ... with a 23,560-token budget and 100 turns; CLM's editing turns do not count toward the turn limit."
- "When a request would exceed the budget, CLM rolls back the last turn and retries, up to six times."
- "An answer is correct only if Qwen3.5-27B, judging at temperature 0 with the BrowseComp-Plus grading template, marks it correct and complete."

**The conflict.** The paper's budget (23,560 tokens) and turn cap (100) differ from `bcp.yaml` (28,672 tokens, 500 steps). See decision 3.

## Retrieval: what the prebuilt BM25 index needs
- **The index:** `Tevatron/browsecomp-plus-indexes`, `bm25/*`, **2.17 GB**. That is over your 1 GB limit, so it needs your approval.
- **The searcher:** BrowseComp-Plus's `searcher/searchers/bm25_searcher.py` (`pyserini` `LuceneSearcher`) needs **Java 21**, and this Mac has no Java. Its tool server, `searcher/mcp_server.py` (`fastmcp`), cuts snippets with the **Qwen/Qwen3-0.6B tokenizer** (`--snippet-max-tokens`, default 512; `--k`, default 5).
- **The proposal:**
  - install Java 21 (Homebrew `openjdk@21`, about 0.3 GB);
  - install `pyserini` and `fastmcp` in their own environment, `~/econo/bcp/.venv`, outside our repo and our `.venv`;
  - run the searcher locally as a small HTTP service, with k = 10, snippets of 512 tokens, and the reader capped at 8,192 tokens (the paper's App. E).

## The two arms
- **base (our harness).** It gets two function tools: `search(query)`, which returns the top 10 as docid and snippet; and `get_document(docid)`, which returns the text capped at 8,192 tokens. The schemas follow BrowseComp-Plus's `searcher/tools.py`, because CLM's own skill schemas are not public. The budget rule is the same as on TBLite.
- **clm.** CLM's agent as released, through our gateway, with `--clm-config bcp` and the same two tools as a skill. We write the skill (scripts calling the local search service), because CLM's is not released.
- **One Harbor task per question.**
  - The instruction is the question, plus a brief: the tools, the answer file, and the answer format (BrowseComp's "Explanation / Exact Answer / Confidence").
  - The question text lives only in `~/econo/bcp/`, never in the repo.
  - The task's verifier only collects the answer file; the judge grades it afterwards.

## Questions
- **The split:** a seeded split now. Shuffle the 830 IDs with `random.Random(20261013)`; the first 150 are **test**, the next 50 are **development**.
- **The pilot:** the first 10 development questions.
- **What is committed:** only the IDs.

## The run (after your decisions)
- **Pilot:** 10 questions × 2 arms (base, clm) × 1 repeat, at the budget you choose, all through the gateway.
- **Checks and report:** every call is validated token for token. The report is per arm:
  - accuracy;
  - calls, searches and reads per question;
  - PFLOPs per question;
  - peak prompt;
  - CLM edits or compactions;
  - wall time;
  - the projection.
- **The projection's inputs:** 150 × 3 arms × 3 repeats = 1,350 runs. At about 25–60 calls per question (12f/12g), that is about 34,000–81,000 calls. Purdue paces at 20 a minute, and the gateway's default daily cap is 8,000 requests. That is about 4–10 days of calls before judging. The pilot will measure the real number.

## Decisions needed
1. **The judge.** CLM used Qwen3.5-27B. **Purdue does not serve it**, per its model list of 10 October (`runs/purdue-smoke/models.json`); I can refresh the list with one models request through the gateway. The options:
   - **A. `qwen3:32b` on Purdue, with BrowseComp-Plus's grading template.** This is BrowseComp-Plus's own current judge: its `docs/llm_as_judge.md` says it moved to Qwen3-32B, within 1% of GPT-4.1. It is reproducible and served. **(I recommend this.)**
   - **B. `qwen3.8:27b`,** the agent's own model. It is the nearest family to Qwen3.5-27B, but the agent would be judging itself.
   - **C. `gpt-oss:120b` on Purdue.**
   - **D. Qwen3.5-27B on our own vLLM** (Tier B). It is not available today.
   - In every case, I would also report a plain exact-match column as a check.
2. **The task directories and the search skill must be built by us,** following App. E and BrowseComp-Plus's tools, because CLM did not release them. Is that acceptable as "CLM as released"? Its agent and config would be untouched.
3. **Budget and steps.** The paper says 23,560 tokens and 100 turns; `bcp.yaml` says 28,672 tokens and 500 steps.
   - I propose **the paper's**, because its reported BrowseComp-Plus results use it.
   - CLM generates up to **4,096 tokens per call** on BrowseComp-Plus, but our gateway pins 2,048 for every arm. I propose 4,096 for both arms on BrowseComp-Plus (`--max-tokens 4096`).
4. **Retrieval:** k = 10, snippets of 512 tokens and the 8,192-token reader cap (CLM's App. E), rather than BrowseComp-Plus's default of top 5.
5. **Downloads and installs:**
   - the 2.17 GB BM25 index;
   - Java 21, about 0.3 GB;
   - the Qwen3-0.6B tokenizer, a few MB;
   - `pyserini` and `fastmcp`, in `~/econo/bcp/.venv`.
6. **The CLM pin** moves to `648a48b`: no change to its harness.
