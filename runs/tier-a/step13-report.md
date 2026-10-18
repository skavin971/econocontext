# Step 13 report: BrowseComp-Plus setup (13a) and does our data support forecast-driven context? (13b) (2026-10-13)

> **Erratum (2026-10-18).** The retrieval row below cites the CLM paper's Appendix E for BM25. That is wrong: Appendix E gives only "the top 10 snippets of 512 tokens" and the 8,192-token reader cap, and the paper never names its retriever. BM25 was our own choice in the 13a plan (`edad515`), presented without the dense alternative (BrowseComp-Plus also ships Qwen3-Embedding indexes; its paper reports GPT-5 at 55.9% with BM25 and 70.1% with Qwen3-Embedding-8B). Every BrowseComp-Plus result from steps 13 to 19 is a **BM25** result. CLM's repository (`648a48b`, and its head `4831982`) contains no BrowseComp-Plus search code or retriever.

**Branch:** `agentdb-12a`.
- `ace1995` was pushed after the secrets gate.
- 13a and 13b are one local commit, **not pushed**.
- **No change to EconoContext.**
- **Changes elsewhere:**
  - `gateway/`: a per-benchmark output cap and log directory, and judge calls passed through;
  - `harness/`: a BrowseComp-Plus tool set;
  - new `benchmarks/bcp/`;
  - the CLM pin.
- **Tests:** 148 pass.

## Status in one paragraph
**The BrowseComp-Plus setup works end to end:** search, both arms, the answer file and the judge. But **the first pilot question found a setup problem: 10 of CLM's 65 calls fail token-for-token validation** (base: 5 of 5 valid). Purdue's server tokenizes letters with combining marks (Devanagari, in a Wikipedia document) differently from the model's own tokenizer, which we use to rebuild prompts. Your rule says to stop on failing validation. So **the pilot stopped after 1 of 10 questions, and 13b's BrowseComp-Plus part was skipped.** 13b's TBLite part is complete. A decision is needed below.

---

## 13a: BrowseComp-Plus on Tier A

### The setup, as decided

| item | value | source |
|---|---|---|
| judge | **`qwen3:32b`**, BrowseComp-Plus's grading template (verbatim) and its evaluator's sampling (temperature 0.7, top_p 0.8, top_k 20, up to 4,096 tokens); plus exact match | Purdue's model list (through the gateway, 2026-10-13, `step13a-2-models.out`): 33 models, **no Qwen3.5-27B**, `qwen3:32b` served |
| packaging | **"CLM agent as released; task and search packaging built from Appendix E"**: one Harbor task per question; CLM's search skill `benchmarks/bcp/skill/bcp_search` (`bcp_search`, `bcp_get_document`, the names in the CLM paper's Figure 3) | CLM did not release its BrowseComp-Plus tasks or skill; Harbor has no adapter |
| budget | 23,560 with a 2,048 reserve (limit 21,512); 100 turns | CLM paper, App. E; the paper states no other reserve, and `bcp.yaml` has 2,048 |
| output cap | 4,096 on BrowseComp-Plus for every arm (gateway `--max-tokens-for bcp=4096`); TBLite stays at 2,048 | your decision |
| retrieval | BM25, prebuilt index; top 10, 512-token snippets, documents capped at 8,192 tokens (Qwen3-0.6B tokenizer, as BrowseComp-Plus's tools) | k, snippet and reader cap: App. E. **BM25: our choice, not App. E (erratum above).** The 12g audit's runs used 5 results. |
| observations | up to 60,000 characters for both arms | CLM's `bcp.yaml` ("search results and documents arrive whole") |
| CLM pin | `648a48b`; `clm/clm_harness` unchanged from `18dc111` | `step13a-3-clm-pin.out` |
| split | seed 20261013: 150 test, then 50 development; the pilot is the first 10 development questions (IDs only, `benchmarks/bcp/split.json`) | `step13a-4-split.out` |

**What was built:**
- **The search service,** `benchmarks/bcp/search_server.py`. It runs in its own environment, `~/econo/bcp/.venv`, with Java 21 and pyserini 2.4.0, bound to 127.0.0.1. Both arms use it: CLM through the skill (containers reach it via `host.docker.internal`), and our harness through two function tools.
- **Our harness's tools:** `tool_set="bcp"`, with BrowseComp-Plus's tool descriptions verbatim. TBLite's tool set is unchanged.
- **Code:** the task builder (`tasks.py`), the runner (`run.py`) and the judge (`judge.py`); BrowseComp-Plus's prompts are in `prompts.py`, MIT, with `LICENSE-browsecomp-plus`.
- **Where the data lives:** everything holding BrowseComp-Plus text stays in `~/econo/bcp/`: the tasks, the trials, and the gateway logs of `bcp*` runs (`--log-dir-for bcp=…`; also git-ignored). Only numbers and IDs are committed.

**Downloads and installs** (`step13a-1-installs.out`): Java 21 (Homebrew), the BM25 index (2.0 GB on disk), pyserini, fastmcp and the Qwen3-0.6B tokenizer. Free disk went from 41 GB to 35 GB.

**One search, end to end** (`step13a-5-search-check.out`):
- 10 hits, 5,689 Qwen tokens of output per search.
- A document read was capped (`truncated: true`).
- The search was reached from a container too.

### The pilot: question 1 of 10 (`step13a-8-pilot-report.out`)

| arm | judged correct | answer file | calls | searches | reads | PFLOPs | peak prompt | CLM edits / rollbacks | wall time | calls valid |
|---|---|---|---|---|---|---|---|---|---|---|
| base | 0/1 | no | 5 | 4 | 0 | 1.84 | 24,689 | – | 3.6 min | 5/5 |
| clm | 0/1 | yes | 65 | 39 | 0 | 15.09 | 23,173 | 6 / 6 | 24.8 min | **55/65** |

- **base** ended at the budget after 4 searches: each search adds about 5,900 tokens and the limit is 21,512. It never wrote an answer file, so it is counted wrong and was not judged. Its final turn, allowed past the limit as on TBLite, reached a 24,689-token prompt. This is what Base without context management does under this budget.
- **clm** ran 39 searches and no document reads, made 6 edits and 6 rollbacks, ended at the budget, and wrote an answer; the judge marked it wrong.
- **The judge** ran once (on CLM's answer), and its verdict parsed. It ran under the run ID `bcp13p.judge`, outside agent FLOPs.
- **The projection,** from this one question and so only indicative: about 35 calls and 14 minutes per run. That gives about 48,600 requests for 150 × 3 × 3, about **6 days** at 8,000 a day, and about 13 days of trials run one at a time.

### The setup problem (`step13a-8-pilot-report.out` and this section)
- **Where:** calls 27–36 of CLM's run fail validation, each with dP = +18; every other call (1–26 and 37–65) passes. In those 10 calls, the prompt holds a Wikipedia document with a Devanagari name; CLM's edit removed it after call 36.
- **What differs:** the decoded text is identical on both sides, but the split is not. **Locally**, `वी` (U+0935 U+0940) is one token. **Purdue's server** makes it two: `व` and `ी`.
- **Our side:** the local split follows the model's own `tokenizer.json` at the measured revision (`017b9c7`). Its pre-tokenizer is `[^\r\n\p{L}\p{N}]?[\p{L}\p{M}]+`, which keeps letters and combining marks together. `transformers` and the raw `tokenizers` library agree.
- **The server's side:** it splits off the combining marks, as a pattern without `\p{M}` (older Qwen tokenizers) would. Latin text is unaffected: none of TBLite's 2,194 calls failed. Any text with combining marks (Devanagari, Thai, Arabic diacritics, accents in decomposed form) can trigger it, and BrowseComp-Plus's corpus has such text.
- **Size:** 18 tokens in about 11,000 (0.16%) for these calls. Our FLOPs measure also computes a variant on the server's own token ids (`F_server`), which is unaffected.

**Options (your decision):**
1. **Accept segmentation-only differences.** A call validates if the decoded text is identical and the only differences are in how a span is split. For BrowseComp-Plus, price FLOPs on the server's own token ids. (My recommendation: it keeps the token-for-token check on the text and uses the server as ground truth.)
2. **Match the server's tokenizer.** Find the pre-tokenizer Purdue serves (for example, ask Purdue, or try older Qwen pattern revisions) and rebuild locally with it. That is exact, but Purdue's internals are outside our control.
3. **Keep the strict rule, and drop or flag affected calls.** That loses about 15% of CLM's calls on this question, more on documents in other scripts.

The same difference affects `measure/tokens.py count_text` (EconoContext's counts) and `count_prompt` (the budget gate) on such text, by the same small amount.

---

## 13b: does the data support forecast-driven context? (TBLite; offline; **a replay of fixed trajectories, not live behaviour**)

### The answer (130 TBLite runs, limit 29,952 tokens; `step13b-1-tblite.out`, `runs/tier-a/step13b/tblite/summary.md`)
- **FLOPs saved against (a) no eviction:**
  - **(b) oldest first: −14.8%** (it costs more);
  - **(c) knowing: −8.1%** (it also costs more).
- **Re-fetches caused by (b): 56** extra calls ((c): 0).
- **The gap between (b) and (c): 6.7% of (a)'s FLOPs.** This is the room a forecaster has, but measured against a policy that already loses: under this rule, knowing loses less, and neither beats doing nothing.
- **Calls over the limit:**
  - (a) 313;
  - (b) 166;
  - (c) 187.

  The total is 2,190 calls. Only outputs of 1,000+ tokens can be evicted, so even (b) and (c) cannot always get under the limit.

**Per step-9 arm (30 runs each):**

| arm | (b) saves | (c) saves | gap | (b) re-fetches |
|---|---|---|---|---|
| raw | −34.4% | −11.2% | **23.2%** | 33 |
| econo+jev | −15.9% | −7.6% | 8.3% | 12 |
| clm-32k | −3.1% | −6.5% | −3.4% | 3 |
| clm-full | −7.7% | −8.7% | −1.0% | 7 |

The 10d and step-11 rows have 1–2 runs each (`summary.md`).

### Why eviction loses here
Under prefix caching, a cached item costs only attention in later calls: about C_attn × its tokens × the query tokens per later call. For a 6,000-token item, that is about 3.9×10⁵ × 6,000 × 2,000 ≈ 5×10¹² FLOPs per call.

Evicting it forces a re-prefill of everything after it. With oldest first, that is most of the prompt: about C_token × 18,000 ≈ 9×10¹⁴ FLOPs, once.

Paying that back takes on the order of 190 later calls, and TBLite runs have about 15–60. So **eviction only pays if it prevents running over the limit, or if the evicted item sits near the end of the prompt.** Where an item is needed again, (b) also re-fetches it: one run (raw, malicious-package r1) thrashes, with 20 re-fetches and 3.9× the FLOPs.

### Items needed again (TBLite)
- **236 items** (tool outputs of 1,000+ tokens): **49.6% needed again, 50.4% never.**
- **Rule:** a later turn quotes 30+ characters, or names a path or symbol that, at that moment, only the item contains.
- **When:** the first need comes at a median of **1 turn** after arrival (quartiles 1–2, range 1–10). Items that matter are used at once; the others are not used again.
- **The rebuild check:** for the arms with no context management (raw, raw-32k, base-32k), the replayed prompts equal the logged prompts **token for token on 100% of calls.**

**The replay's assumptions:**
- Every logged call keeps its logged G.
- A re-fetch is one extra call generating the run's median G, which re-shows the item where it then stays.
- An evicted item becomes a one-line marker.
- The cache simulator prices every rebuilt prompt.

### The BrowseComp-Plus part
**Skipped**, as you instructed, because of the validation problem above. The script is ready: `runs/tier-a/scripts/step13b_forecast.py bcp`, including the repeat statistics (results the question already had, and whether the first copy was still in the prompt).

## What I need from you
1. **The validation rule for non-Latin text** (options 1–3 above). The rest of the pilot (9 questions × 2 arms, about 5 hours) and 13b's BrowseComp-Plus part wait on this.
2. **Whether to read 13b's TBLite answer as it stands, or to replay a second eviction rule.** Under this rule (evict at 80% down to 60%), eviction never pays on TBLite. A rule that evicts only near the end of the prompt, or only when the next call would otherwise pass the limit, might.

The gateway (port 8787) and the search service (port 8790) are still running.
