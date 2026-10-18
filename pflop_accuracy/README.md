# PFLOP accuracy: checking our PFLOPs math with CLM

This folder bundles what I use to check our PFLOPs measurement against a real server. It runs **CLM's agent on its own** (zero-shot, as its authors released it) on 8 BrowseComp-Plus questions. Then it compares our numbers with what the server actually did.

It's self-contained: it doesn't depend on EconoContext or any of the other experiments in the repo. Its runs are labelled `bcp-pflop-…`, so they stay separate from everything else. Feel free to use it, change it, or build on it for your own work.

## What it checks
We price every model call with the CLM paper's prefix-reuse FLOPs (Appendix C, Eq. 9):

    F = C_token·(P − R + G) + C_attn·[½(P² − R²) + G·P + ½·G²]

- **P:** prompt tokens.
- **G:** generated tokens.
- **R:** prompt tokens served from the prefix cache.

Purdue's API shows P and G but **not R**: it doesn't report cache hits, so I estimate R with a simulator (`measure/cache_sim.py`). A vLLM server we run ourselves, with the same model, reports R directly. So the comparison is:

| check | what is compared | where |
|---|---|---|
| **Cache hits** (the interesting one) | the simulated R vs vLLM's real `cached_tokens`, call by call | vLLM only |
| **Tokens** | our rebuild of each prompt vs the server's own token ids | both |
| **Totals** | our PFLOPs vs PFLOPs from the real cache hits, and vs CLM's own FLOPs export | both (real cache: vLLM) |

**The Purdue path is mostly a smoke test.** It confirms the setup works and the tokens line up, but it can't say anything about the cache.

**Not covered here:**
- answer accuracy: retrieval is BM25 and nothing is tuned;
- EconoContext;
- the FLOPs constants, which come from the model's architecture and are already checked in `tests/measure/`.

## What you'll need
- **System:** Linux (I've been using Chameleon), Docker Engine (`docker info` working for your user), Python 3.12, Java 21 (`sudo apt install openjdk-21-jdk`), and about 15 GB of free disk.
- **For the Purdue path:** your own Purdue GenAI Studio key, in `.env` as `GENAI_API_KEY=…`. `.env` is git-ignored.
- **For the vLLM path:** one 80 GB GPU (A100 80GB or H100) and vLLM 0.30.0, the version Purdue runs.

The repo tracks a lot of old run logs, so a partial clone saves time:

    git clone --filter=blob:none https://github.com/skavin971/econocontext.git && cd econocontext

## Setup (once)
    python3.12 pflop_accuracy/setup.py

This installs the three environments, clones BrowseComp-Plus, decrypts its questions (about a 2.8 GB download), downloads the BM25 index (about 2.2 GB) and writes the task folders.
- It's safe to rerun.
- `--check` only reports what's there.
- Everything from BrowseComp-Plus lands in `~/econo/bcp/`, outside the repo.

## Path A: through Purdue
This is easiest with three terminals.

**A1.** The search service:

    JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 ~/econo/bcp/.venv/bin/python benchmarks/bcp/search_server.py \
        --index ~/econo/bcp/BrowseComp-Plus/indexes/bm25 --host 0.0.0.0

**A2.** The gateway:

    .venv/bin/python -m gateway.server --provider purdue --max-tokens-for bcp=4096 \
        --log-dir-for bcp=~/econo/bcp/gateway --max-calls-per-run 300

**A3.** A quick readiness check, one question to start, then all 8:

    python3 pflop_accuracy/check_ready.py --backend purdue
    .venv/bin/python pflop_accuracy/run_clm.py --backend purdue --questions 1
    .venv/bin/python pflop_accuracy/run_clm.py --backend purdue --label bcp-pflop-purdue-<date>-8

**A4.** The PFLOPs check, with the label the run printed:

    .venv/bin/python pflop_accuracy/check_pflops.py --label bcp-pflop-purdue-<date>

Purdue allows 20 requests a minute and 8,000 a day per key. 8 questions take about 500 requests and 2–3 hours.

## Path B: through our own vLLM
**B1.** Serve the model, at the same revision our FLOPs constants were measured on:

    pip install vllm==0.30.0      # in its own environment
    vllm serve Qwen/Qwen3.8-27B-FP8 --revision 017b9c7af6b5689d5dd426a76e0bc077eb5ca20a \
        --served-model-name qwen3.8:27b --max-model-len 65536 \
        --enable-prefix-caching --enable-prompt-tokens-details \
        --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3 --port 8000

- `--enable-prompt-tokens-details` is the flag that makes vLLM report cache hits.
- The two parser flags are my best guess from the model's chat template; Purdue doesn't publish its serving flags. If tool calls don't parse, a different parser is probably needed. I'd be glad to hear which one works.

**B2.** The search service, as in A1.

**B3.** The gateway, pointed at vLLM:

    .venv/bin/python -m gateway.server --provider vllm --upstream http://127.0.0.1:8000/v1 --rpm 600 \
        --max-tokens-for bcp=4096 --log-dir-for bcp=~/econo/bcp/gateway --max-calls-per-run 300

**B4.** A readiness check, a one-minute cache probe, then the run:

    python3 pflop_accuracy/check_ready.py --backend vllm
    .venv/bin/python pflop_accuracy/probe_vllm_cache.py
    .venv/bin/python pflop_accuracy/run_clm.py --backend vllm

**B5.** The PFLOPs check:

    .venv/bin/python pflop_accuracy/check_pflops.py --label bcp-pflop-vllm-<date>

The cache numbers are cleanest with one batch at a time and nothing else running on that vLLM server, so no other traffic shares or evicts the cache.

## Reading the result
`check_pflops.py` prints a row per question and a short summary:
- **Tokens:** "All match" means our prompt rebuild equals the server's token ids. Any mismatches are listed.
- **Cache hits:** the share of calls where the simulated R equals vLLM's, the largest gap, and the first call that differs. 100% would mean the simulator reproduces the real cache. Anything less is interesting in itself, since it shows where the simulator and vLLM disagree.
- **PFLOPs:**
  - ours vs with the real cache, as a ratio (1.0000 means identical);
  - ours vs CLM's own export, which uses vLLM's cache counts when they're available.

The summary is also saved to `runs/pflop-accuracy/<label>.md`. It holds numbers and question ids only, so it's easy to share if you want to compare notes.

## Good to know
- **BrowseComp-Plus asks that its text never appear online.** That covers questions, documents, answers and model outputs, and the data carries a canary string for this. Everything stays in `~/econo/bcp/`, and `.gitignore` keeps it out of the repo. The saved summaries contain no BrowseComp-Plus text.
- **Labels are never reused:** `run_clm.py` won't overwrite an earlier batch, so pass a new `--label` for each one.

---
Background, if you want to dig in:
- `measure/flops.py`: Eq. 9, the token rebuild and its validation;
- `measure/cache_sim.py`: the simulator (16-token blocks, a chain of block hashes, vLLM's last-token rule);
- `docs/tier-a-decisions.md`: the "PFLOP accuracy" section.
