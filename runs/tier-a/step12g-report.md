# Step 12g report: BrowseComp-Plus trajectories (offline, 2026-10-12)

**Branch:** `agentdb-12a`.
- 12d and 12f (`401773a`, `6a7bc60`) were pushed after the secrets gate (`step12f-7-push.out`).
- 12g is one local commit, **not pushed**.

The step made no model calls and changed none of our code. The only addition is an analysis script, `runs/tier-a/scripts/step12g_bcp.py`. Tables are in `runs/tier-a/step12g/summary.md`; per-question numbers are in `questions.csv`.

**No benchmark text is in our repository.** BrowseComp-Plus asks, through a canary string in its files, that its data never appear online as plain text, and our repository is public. The decrypted files stay in `~/econo/bcp/`, and only numbers and question IDs were written here.

## What was fetched
- **The repository:** `github.com/texttron/BrowseComp-Plus` at `0469490`, cloned to `~/econo/bcp/BrowseComp-Plus`.
- **The released run files:** the four commands of `scripts_build_index/download_run_files.sh`. Each downloads one file from the Hugging Face dataset `Tevatron/browsecomp-plus-runs` and XOR-decrypts it with the published canary. I ran the commands myself, with our `.venv` Python in isolated mode (`-I`), after reading the script. The four files are 0.9 GB in total, in `data/decrypted_run_files/`.
- **The indexes:** not downloaded.
- **The questions,** needed so that the labels can tell a name taken from a result apart from one taken from the question:
  - only the `query_id` and `query` columns of `Tevatron/browsecomp-plus`, read over HTTP range requests rather than its 2.8 GB of documents;
  - decrypted to `~/econo/bcp/BrowseComp-Plus/data/queries_decrypted.jsonl`, outside our repo (`step12g-3-queries.out`).

## Licences (`step12g-2-licences.out`)
- **Code:** MIT, "Copyright (c) 2025 texttron" (`LICENSE`).
- **Questions, answers and judgements** (`Tevatron/browsecomp-plus`): MIT.
- **The corpus** (`Tevatron/browsecomp-plus-corpus`): MIT.
- **The run files** (`Tevatron/browsecomp-plus-runs`): **no licence declared** on the dataset card. Using them beyond this offline analysis needs that settled.

## Which baselines have trajectories
The paper (arXiv 2508.06600, Table 1) evaluates 26 model–retriever pairs. **Only four have released trajectories:** GPT-5 and o3, each with BM25 and with Qwen3-Embedding-8B. None of the others (gpt-4.1, Sonnet 4, Opus 4, the two Gemini 2.5 models, gpt-oss-120B, Qwen3-32B, SearchR1-32B) is in the run repository.

| model | retriever | questions | completed | accuracy | evidence recall | search calls per question |
|---|---|---|---|---|---|---|
| GPT-5 | Qwen3-Embed-8B | 830 | 828 | 70.12% | 78.98% | 21.74 |
| o3 | Qwen3-Embed-8B | 830 | 810 | 63.49% | 73.24% | 23.97 |
| GPT-5 | BM25 | 830 | 828 | 55.90% | 61.70% | 23.23 |
| o3 | BM25 | 830 | 806 | 49.28% | 56.64% | 25.93 |

Accuracy, recall and search calls are the paper's figures. The question counts and "completed" come from the files. My count of search calls for GPT-5 + BM25 (19,280 ÷ 830 = 23.23) matches the paper.

**The setup these runs use:** search only. Each search returns the top 5 documents, each cut to its first 512 tokens. **There is no get_document call in any of the four runs.** The paper treats the whole-document reader only as an ablation, whose trajectories are not released.

## The three strongest runs, per question: mean (median)
Token counts use our Qwen tokenizer.

| run | turns | searches | document reads | search-result tokens | of which document snippets | model's own text | reasoning as included | documents returned ≥ 2 times | tokens of those repeats |
|---|---|---|---|---|---|---|---|---|---|
| GPT-5 + Qwen3-Embed-8B | 23 (23) | 22 (22) | 0 | 62,585 (63,006) | 53,473 (54,023) | 839 (802) | 1,399 (1,049) | 17 (16) | 32,777 (31,654) |
| o3 + Qwen3-Embed-8B | 25 (27) | 24 (26) | 0 | 68,655 (75,354) | 58,768 (64,464) | 623 (605) | 781 (536) | 20 (20) | 32,338 (33,064) |
| GPT-5 + BM25 | 24 (25) | 23 (24) | 0 | 69,439 (71,819) | 59,641 (61,767) | 856 (831) | 1,534 (1,140) | 19 (19) | 30,108 (27,506) |

- **Turns** are the tool calls plus the final answer.
- **The model's own text** is its search queries plus its final answer.
- **Reasoning:** the files carry only reasoning summaries. The full reasoning of GPT-5 and o3 is hidden, so its size is unknown.
- **Repeated documents:** no document is ever *opened*. "Documents opened more than once" therefore becomes **documents returned again by a later search**, and that is very common. 51–61% of all returned results are documents the same question had already received (`step12g-5-checks.out`). Their snippets are re-sent each time.
- **Context size:** with no context management in these runs, a question's context grows to about 63–70k tokens of search results.

## Labels (share of turns; share of the tokens the turns add to the context)

| run | (a) follows a name from a result | (b) opens a listed document | (c) new search | (d) answer |
|---|---|---|---|---|
| GPT-5 + Qwen3-Embed-8B | 12.1%; 12.6% | 0%; 0% | 83.6%; 87.0% | 4.4%; 0.4% |
| o3 + Qwen3-Embed-8B | 10.3%; 10.6% | 0%; 0% | 85.8%; 89.0% | 3.9%; 0.3% |
| GPT-5 + BM25 | 9.0%; 9.3% | 0%; 0% | 86.9%; 90.3% | 4.1%; 0.4% |

- **(a)** is a search whose query contains a specific name that appears verbatim in an earlier result, and not in the question or in the model's earlier queries. A specific name is a run of 2+ capitalized words, a URL, a letter-and-digit ID, or a quoted phrase holding one of these.
  - A first, looser rule also counted single capitalized words and any quoted phrase. A local spot check showed it matched words like "University", "Turkey" and "we were", so it is kept only as an **upper bound: (a) 17–21% of turns** (`step12g-4-analysis-loose-names.out`).
  - The strict rule's matches, checked locally, are names of people, teams and places first seen in a result.
- **(b) is 0 by construction:** these runs have no document reader.
- **(c) and (d) cover the rest.** A search turn adds its query plus 5 snippets (about 2,850 tokens). The answer adds a few hundred.

## ESTIMATE: what share of a trajectory's tokens an engine could act on
These are shares of the context tokens in the files: the question, the model's text and the tool outputs. They are **not FLOPs**. The system prompt and the hidden reasoning are not in the files, and these models are not Qwen.

| run | (i) big outputs a slicer could shrink | of which document snippets | of which repeats of documents already returned | (ii) turns an engine could take over: (a) + (b) |
|---|---|---|---|---|
| GPT-5 + Qwen3-Embed-8B | 98.5% | about 84% | 51.6% | 12.6% of tokens (12.1% of turns) |
| o3 + Qwen3-Embed-8B | 98.9% | about 85% | 46.6% | 10.6% of tokens (10.3% of turns) |
| GPT-5 + BM25 | 98.6% | about 85% | 42.8% | 9.3% of tokens (9.0% of turns) |

- **(i)** Every search result is a big output: 5 snippets of up to 512 tokens plus the JSON around them, about 2,850 tokens. A slicer would act on nearly the whole context.
  - The clearest part needs no prediction at all: **43–52% of the context is snippets of documents the model had already been shown.** Replacing each repeat with a one-line reference loses nothing while the first copy is still in context.
- **(ii)** With (b) absent, an engine could take over the (a) turns: **9–13% of turns and tokens** under the strict rule, and at most 18–22% under the loose one.
  - This is optimistic. Even an (a) turn needs a choice of which name to follow and how to phrase the query. Our label only shows that the name was already visible.
  - With a document reader (the paper's ablation, and the CLM paper's setup), opening a listed document would be a (b) turn, and that share could be much larger. These runs cannot show it.

**Against TBLite (12d, 12f):**
- big outputs are about 15% of FLOPs there, against about 99% of context tokens here;
- engine-takeable turns are at most 0.1–3% of FLOPs there, against about 9–13% of tokens here (up to 22%).

So the opportunity depends on the benchmark, as 12f's scope note said.

## Checks (`step12g-5-checks.out`)
- **Repeated results, all three runs:**
  - GPT-5 + Qwen3-Embed-8B: 90,230 results returned, 34,953 distinct within their question (61.3% repeats).
  - o3 + Qwen3-Embed-8B: 99,465 and 44,746 (55.0%).
  - GPT-5 + BM25: 96,400 and 47,545 (50.7%).
- **Line splitting:** the run files hold Unicode line separators inside their JSON. The script splits on `\n` only, after a first attempt that used `splitlines()` failed.
