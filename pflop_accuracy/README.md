# PFLOP accuracy: check our PFLOPs math with CLM

**This folder is the only thing you need.** It runs **CLM's agent only** (zero-shot, as released by its authors) on 8 BrowseComp-Plus questions, then checks **our PFLOPs measurement** against a real server. It does not touch EconoContext, our own agents, or any of our numbered experiment steps. All its runs are labelled `bcp-pflop-…` and kept apart.

## 1. What this checks
We price every model call with the CLM paper's prefix-reuse FLOPs (Appendix C, Eq. 9):

    F = C_token·(P − R + G) + C_attn·[½(P² − R²) + G·P + ½·G²]

- **P:** prompt tokens.
- **G:** generated tokens.
- **R:** prompt tokens served from the prefix cache.

On Purdue's API we can see P and G, but **not R**: Purdue doesn't report cache hits, so R comes from our simulator (`measure/cache_sim.py`). On our own vLLM, which serves the same model, the server reports R itself. So this step checks:

| check | what is compared | where |
|---|---|---|
| **1. Cache hits** (the main one) | our simulated R vs vLLM's real `cached_tokens`, call by call | Path B only |
| **2. Tokens** | our rebuild of each prompt vs the server's own token ids | both paths |
| **3. Totals** | our PFLOPs vs PFLOPs from the real cache hits, and vs CLM's own FLOPs export | both (real cache: Path B) |

**Path A (Purdue) is only a smoke test.** It shows the setup works and that the tokens match; it can't check the cache.

**What this does not test:** answer accuracy (BM25 retrieval, not tuned), EconoContext, or the FLOPs constants. The constants are fixed by the model's architecture and already checked in `tests/measure/`.

## 2. Requirements
- **Hardware and system:** a Linux machine (Chameleon), Docker Engine (`docker info` works for your user), Python 3.12, Java 21 (`sudo apt install openjdk-21-jdk`), and about 15 GB of free disk.
- **Path A:** your **own** Purdue GenAI Studio key, in `.env` as `GENAI_API_KEY=…`. Never commit `.env`.
- **Path B:** one GPU with 80 GB (A100 80GB or H100), and vLLM **0.30.0**, the version Purdue runs.

Clone without downloading all old run logs:

    git clone --filter=blob:none https://github.com/skavin971/econocontext.git && cd econocontext

## 3. Setup (once)
    python3.12 pflop_accuracy/setup.py

It installs the three environments, clones BrowseComp-Plus, decrypts its questions (a 2.8 GB download), downloads the BM25 index (2.2 GB) and writes the task folders.
- It is safe to rerun.
- `--check` only reports what is ready.
- Everything from BrowseComp-Plus goes to `~/econo/bcp/`, outside the repository.

## 4. Path A: Purdue (smoke test)
Use three terminals.

**A1.** The search service:

    JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 ~/econo/bcp/.venv/bin/python benchmarks/bcp/search_server.py \
        --index ~/econo/bcp/BrowseComp-Plus/indexes/bm25 --host 0.0.0.0

**A2.** The gateway:

    .venv/bin/python -m gateway.server --provider purdue --max-tokens-for bcp=4096 \
        --log-dir-for bcp=~/econo/bcp/gateway --max-calls-per-run 300

**A3.** Check, then run 1 question first, then all 8:

    python3 pflop_accuracy/check_ready.py --backend purdue
    .venv/bin/python pflop_accuracy/run_clm.py --backend purdue --questions 1
    .venv/bin/python pflop_accuracy/run_clm.py --backend purdue --label bcp-pflop-purdue-<date>-8

**A4.** Check the PFLOPs (use the label the run printed):

    .venv/bin/python pflop_accuracy/check_pflops.py --label bcp-pflop-purdue-<date>

Purdue allows 20 requests a minute and 8,000 a day per key. 8 questions need about 500 requests and 2–3 hours.

## 5. Path B: our vLLM (the real check)
**B1.** Serve the model. It's the same model and revision our FLOPs constants were measured on:

    pip install vllm==0.30.0      # in its own environment
    vllm serve Qwen/Qwen3.8-27B-FP8 --revision 017b9c7af6b5689d5dd426a76e0bc077eb5ca20a \
        --served-model-name qwen3.8:27b --max-model-len 65536 \
        --enable-prefix-caching --enable-prompt-tokens-details \
        --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3 --port 8000

- `--enable-prompt-tokens-details` is required: it makes vLLM report the cache hits.
- Purdue's exact serving flags aren't published. If tool calls fail to parse, report it, along with the flag you changed.

**B2.** The search service: as in A1.

**B3.** The gateway, pointed at vLLM:

    .venv/bin/python -m gateway.server --provider vllm --upstream http://127.0.0.1:8000/v1 --rpm 600 \
        --max-tokens-for bcp=4096 --log-dir-for bcp=~/econo/bcp/gateway --max-calls-per-run 300

**B4.** Check, probe the cache (1 minute), then run:

    python3 pflop_accuracy/check_ready.py --backend vllm
    .venv/bin/python pflop_accuracy/probe_vllm_cache.py
    .venv/bin/python pflop_accuracy/run_clm.py --backend vllm

**B5.** Check the PFLOPs:

    .venv/bin/python pflop_accuracy/check_pflops.py --label bcp-pflop-vllm-<date>

**Run one batch at a time, and nothing else on that vLLM server**, so no other traffic shares or evicts the cache being measured.

## 6. Reading the result
`check_pflops.py` prints one row per question and a verdict:
- **Tokens: "All match"** means our prompt rebuild equals the server's ids. Mismatches are reported, not hidden.
- **Cache hits:** the share of calls where our simulated R equals vLLM's, the largest difference, and the first call that differs. **100% means our simulator reproduces the real cache.** Anything less is a finding, not a failure on your part: send it as is.
- **PFLOPs:**
  - ours vs with the real cache, as a ratio (1.0000 = identical);
  - ours vs CLM's own export, which uses vLLM's cache counts when they are available.

## 7. What to send back
Send only the generated files `runs/pflop-accuracy/<label>.md`, plus the probe's printed table. They hold numbers and question ids only. Commit them on a branch or paste them in the chat.

## 8. Rules
- **Never commit, post or paste BrowseComp-Plus text** (questions, documents, answers, model outputs). It lives in `~/econo/bcp/` and carries a canary that asks that it never appear online. `.gitignore` already excludes it.
- **Never commit keys:** they live only in `.env`.
- **Don't edit anything outside this folder.** If something needs a change, report it.
- **A new label for every batch:** `run_clm.py` refuses to reuse one.

---
Background for the curious: `measure/flops.py` (Eq. 9, token rebuild and validation), `measure/cache_sim.py` (the simulator: 16-token blocks, a chain of block hashes, vLLM's last-token rule), `docs/tier-a-decisions.md` ("PFLOP accuracy").
