# Handoff: running the BCP studies on a local Mac with Docker

**Who this is for:** the Claude Code agent that pulls `bcp-doc-labeling` on a Mac with Docker and runs Phase 1. It
lists everything Study 2 depended on, what changes on macOS, and a first-session checklist that ends in a stop and
a report. Phase 1 itself is designed separately.

**Rule that does not change:** BrowseComp-Plus text (questions, answers, documents, traces) never goes into git.
`~/econo/bcp/` holds it and is git-ignored; only code, aggregates, docids and hashes are committed.

## 1. What Study 2 ran on

| Item | Value |
|---|---|
| Code commit of the Study 2 runs | `7325e73` (code unchanged through `5721352`; Phase 0 adds analysis only) |
| Branch | `bcp-doc-labeling` |
| Runner scripts, exactly as run | `scripts/bcp_study2/run_ours.sh`, `run_clm.sh`, `run_search.sh` (paths are the cloud container's) |
| Study 2 trial data (private) | `~/econo/bcp/full-search/study2/`, `~/econo/bcp/runs/bcp-s2/`, `~/econo/bcp/gateway/` |

### Services

| Service | How it ran | Port | Notes |
|---|---|---|---|
| Lucene search | `JAVA_HOME=<java 21> ~/econo/bcp/.venv/bin/python benchmarks/bcp/search_server.py --index ~/econo/bcp/BrowseComp-Plus/indexes/bm25 --host 0.0.0.0 --port 8790` | 8790 | `/health` must say `k 10, snippet_tokens 512, document_tokens 8192, tokenizer Qwen/Qwen3-0.6B`. `--host 0.0.0.0` so CLM's containers can reach it |
| Gateway for our arms (M0–M5) | started in-process by `search_pilot.serve()`: Purdue, `max_tokens_for={"bcp": 4096}`, 120 s timeout, no retries, `max_calls_per_run=110`, `--rpm 8` | random | Logs to `<run folder>/gateway/<run id>/calls.jsonl` |
| Gateway for CLM (C1, C2) | `.venv/bin/python -m gateway.server --provider purdue --port 8787 --max-tokens-for bcp=4096 --log-dir-for bcp=~/econo/bcp/gateway --max-calls-per-run 300 --rpm 12` | 8787 | Q1215's CLM trials used a second copy on 8788 with `--rpm 8` (Purdue's limit is 20 calls/min in total) |
| Docker daemon | `dockerd` (overlayfs), started by hand in the container | – | Needed by CLM only |
| Judge (official) | vLLM `Qwen/Qwen3-32B`, OpenAI-compatible server; `benchmarks/bcp/doc_labeling/study2/regrade.py --route vllm` | 8000 | Not run yet (see §5) |

### The main model

- **Endpoint:** Purdue GenAI Studio, `https://genai.rcac.purdue.edu/api` (OpenAI-compatible `/chat/completions`).
- **Model name:** `qwen3.8:27b` = `Qwen/Qwen3.8-27B-FP8`, revision `017b9c7af6b5689d5dd426a76e0bc077eb5ca20a` (vLLM).
- **Pinned by the gateway** (`gateway/server.py`): `temperature 0.7`, `top_p 0.95`, `reasoning_effort medium`,
  `return_token_ids true`, `chat_template_kwargs.enable_thinking true`; `max_tokens 4096` for run ids starting with `bcp`.
- **Protocol:** `--protocol clm` = budget 23,560 with a 2,048 reserve (prompt limit 21,512), one final turn,
  100 turns (`harness/budget.py:BudgetGate(23560, margin=True)`).

### Environment variable names (values never in git)

| Name | Used by |
|---|---|
| `GENAI_API_KEY` | Purdue (agents, gateway, provisional judge) |
| `TYPESAFE_API_KEY` | Jev (`econocontext/predictor/jev.py`) |
| `JUDGE_API_KEY` | the official vLLM judge, if its server needs a key |
| `BCP_SEARCH_URL` | CLM's search skill inside containers (default `http://host.docker.internal:8790`) |
| `BCP_SEARCH_OUTPUT` | `run_search.sh`: where a run's folder goes |

Keys live in the repo's git-ignored `.env`; `gateway/server.py:read_env` reads it.

### Data (exact sources; checksums in `docs/handoff/data-sha256.txt`)

| Data | Source | Where |
|---|---|---|
| BrowseComp-Plus code | `texttron/BrowseComp-Plus` at `046949032b0328319cc9a02663a759ec601d9402` | `~/econo/bcp/BrowseComp-Plus/` |
| Questions, answers, gold/evidence docids | HF dataset `Tevatron/browsecomp-plus` (snapshot `144cff8e35b5eaef7e526346aa60774a9deb941f`), decrypted with upstream `scripts_build_index/decrypt_dataset.py` | `.../data/browsecomp_plus_decrypted.jsonl` (2.1 GB; plus `queries_decrypted.jsonl`, `answers_decrypted.jsonl`) |
| Prebuilt Lucene BM25 index | HF dataset `Tevatron/browsecomp-plus-indexes`, `bm25/*` (2.1 GB) | `.../indexes/bm25/` |
| Corpus parquet (Study 1's SQLite index only) | HF dataset `Tevatron/browsecomp-plus-corpus` (7 parquet files, 1.7 GB) | `~/econo/bcp/corpus-parquet/data/` |
| Cutting tokenizer | `Qwen/Qwen3-0.6B`, snapshot `c1899de289a04d12100db370d81485cdf75e47ca` | HF cache |
| Main tokenizer | `Qwen/Qwen3.8-27B-FP8`, revision `017b9c7af6b5689d5dd426a76e0bc077eb5ca20a` (pinned in `measure/model_constants.json`) | HF cache |

`pflop_accuracy/setup.py` creates the environments, clones BrowseComp-Plus, decrypts the data and downloads the index;
`--check` only reports. `split.json` (`benchmarks/bcp/split.json`) fixes the pilot (10), dev (50) and test (150) ids.

### Python and Java

| Environment | Python | Key packages |
|---|---|---|
| repo `.venv` | 3.13.17 | transformers 5.19.0, tokenizers 0.23.3, numpy 2.5.4, pytest 9.1.1 |
| `.venv-clm` | 3.12.3 | `clm` 0.1.0 (editable, `baselines/clm/upstream` at `648a48b2b2305624b58204288367026823911828`), harbor 0.16.1, litellm 1.104.2 |
| search `~/econo/bcp/.venv` | 3.12.3 | pyserini 2.4.0, pyjnius 1.9.0, torch 2.14.1+cpu (CPU wheel from the PyTorch CPU index), transformers 5.19.0, fastapi 0.162.0, uvicorn 0.54.0 |
| Java | OpenJDK 21 | pyserini needs 21; set `JAVA_HOME` |

**Kept outside the repo:** `~/econo/bcp/` (a symlink to the repo's git-ignored `econo/bcp/`), the HF cache, `.env`.

### CLM

- **Installed as:** `.venv-clm` with `-e baselines/clm/upstream[hf]` and `-c pflop_accuracy/requirements-clm.txt`.
- **Launched by:** `benchmarks/bcp/run.py --arms clm,clm_instructed --qids <q> --repeat <r> --gateway http://127.0.0.1:8787`. It runs
  `clm-harbor trial start -p <task> -e docker -a clm-minimal -m openai/qwen3.8:27b --clm-config bcp` with these agent kwargs:
  - `context_budget_tokens=23560`, `max_steps=100`;
  - `skill_dirs=benchmarks/bcp/skill/bcp_search`;
  - `cost_metric=flops`, `flops_model_key=27b`, `flops_tokenizer=Qwen/Qwen3.8-27B-FP8`.
- **Tasks:** `~/econo/bcp/tasks/<qid>` and `tasks-instructed/<qid>` (`benchmarks/bcp/tasks.py`). Each image is `FROM python:3.12-slim`, and `environment/docker-compose.yaml` maps `host.docker.internal:host-gateway`.
- **From Docker it needs:**
  - the daemon;
  - the `python:3.12-slim` image;
  - containers that can reach the host's search service on 8790.

## 2. What changes on macOS

- **Images:** `python:3.12-slim` is multi-arch, so Docker Desktop on Apple silicon pulls arm64. Nothing in the task image is
  architecture-specific. If anything is pinned to amd64, run it with `--platform linux/amd64` (emulated, slower).
- **`host.docker.internal`:** Docker Desktop resolves it natively. The `host-gateway` mapping in the task compose file is
  harmless there.
- **Java for the search server:** `brew install openjdk@21`, then `JAVA_HOME=/opt/homebrew/opt/openjdk@21` (`setup.py:java21()` already looks there). pyserini's JVM bridge (pyjnius) needs a native arm64 JDK.
- **torch:** the search environment only needs a CPU build; on macOS the default PyPI wheel is CPU. Don't use the PyTorch CPU index URL, which is Linux-only.
- **Paths:** `/root/econo/bcp` becomes `~/econo/bcp`. The runner scripts in `scripts/bcp_study2/` hard-code `/home/user/skavin971/econocontext`; edit `R=` in each.
- **`run_search.sh`** builds Study 1's SQLite index (`corpus-fts.sqlite`, 4.1 GB, from the parquet files) if it's missing, even when `--search-url` is given. Either download the parquet files and let it build, or call `python -m benchmarks.bcp.doc_labeling.search_pilot --root ~/econo/bcp --output <dir> ...` directly (the script's last line).
- **Sleep:** `run_search.sh` already wraps runs in `caffeinate -i` when it exists.

## 3. Known pitfalls

1. **Purdue drops `additionalProperties`** from tool schemas before templating, so the model never sees it.
   - FLOPs are measured on a copy of the log without it (`search_pilot.server_tools`).
   - `F_server` is unusable because Purdue also reorders tool keys.
2. **Purdue's tokenizer splits combining marks.** `measure/tokens.tokenizer(server="purdue")` reproduces this. A stock vLLM server needs `server="vllm"`, otherwise FLOP validation fails on such text.
3. **Jev's limits:** 64k Jev tokens per request and 32k per question, at 2.3 characters per Jev token. Nothing is ever truncated: an oversized unit raises.
   - At most 255 options per `choice` (Strict Rank's chunk limit).
   - Jev's response `model` field is not logged: `Jev.ask` keeps only answers and usage.
4. **Judge timeouts:** Purdue's `qwen3:32b` was unreachable on 10-25 (no reply in 180 s) and answered on 10-27 in 6–60 s. It's an Ollama build: it ignores vLLM's `chat_template_kwargs` and obeys `"think": false`. Use it only as a provisional judge.
5. **Rate limit:** Purdue allows 20 calls a minute in total. Share it across processes with `--rpm`.
6. **`pkill -f <pattern>` kills your own shell** when the pattern is in your own command line. Kill by PID instead.
7. **Disk:** the CUDA build of torch fills a small disk. Install the CPU build.

## 4. Parity fixtures (no BCP text)

`tests/fixtures/parity.json` and `scripts/check_parity.py` check a new environment against the cloud one:

- **Search:** 20 neutral queries, written for this check and not derived from any BCP question. Their top-10 docids (in order) and snippet sha256s.
- **Chunks:** 5 documents' view sha256s, their 400-token document chunks (main tokenizer, as the arms cut them) and their 102-token snippet chunks (Qwen3-0.6B). Each chunk is recorded as chunk id, character span and sha256.
- **Tokens:** 6 fixed strings counted by both tokenizers.
- **FLOPs:** Eq. 9 totals (P, R, U, G, F) of a synthetic 3-call log.

Run it with:

```bash
.venv/bin/python scripts/check_parity.py --search-url http://127.0.0.1:8790   # prints "parity OK"
```

## 5. Judge regrade (T2), ready to run

On a GPU host, start the official judge and regrade:

```bash
vllm serve Qwen/Qwen3-32B --port 8000                      # bf16, as BrowseComp-Plus's evaluate_run.py uses vLLM
.venv/bin/python -m benchmarks.bcp.doc_labeling.study2.regrade --route vllm \
    --base-url http://<gpu-host>:8000/v1 --study s2 s1      # JUDGE_API_KEY if the server needs one
```

- **Where verdicts go:** `~/econo/bcp/judge-phase0/vllm/`. Each holds the model, backend, quantization, template sha256 and sampling. Reruns resume.
- **The provisional Purdue verdicts** are in `~/econo/bcp/judge-phase0/purdue/`.
- **The verdict files hold judge text,** so they stay private. Only aggregates are committed.

## 6. First session checklist (then stop and report)

1. **Pull:** `git fetch origin bcp-doc-labeling && git checkout bcp-doc-labeling`.
2. **Environment:** create `.env` with `GENAI_API_KEY` and `TYPESAFE_API_KEY`, never echoing the values. Then run `python pflop_accuracy/setup.py --check`, then without `--check`.
3. **Data:**
   - confirm `~/econo/bcp/BrowseComp-Plus` is at the commit above;
   - decrypt the data and download `bm25/`;
   - check the files against `docs/handoff/data-sha256.txt` (`shasum -a 256 -c`, run from `~/econo/bcp`).
4. **Services:**
   - start the search server (`/health` as in §1);
   - start Docker Desktop;
   - start the CLM gateway only when CLM runs.
5. **Tests:** `.venv/bin/python -m pytest -q --noconftest tests/benchmarks/test_bcp_*.py`. In the cloud: 95 passed. The rest of `tests/` also needs `harbor`, `agent_db` and `openai` in `.venv` (in the cloud: 175 passed, 3 failed and 8 modules uncollectable, all for those missing packages).
6. **Parity:** `scripts/check_parity.py` must print `parity OK`.
7. **One M0 smoke trial** on a dev question that isn't in the pilot set (any id from §7 of `docs/results/2026-10-27-phase0/README.md`), under `--protocol clm`:
   ```bash
   BCP_SEARCH_OUTPUT=~/econo/bcp/full-search/phase1-smoke bash scripts/bcp_study2/run_search.sh \
       --query-ids <dev id> --arms full --search-url http://127.0.0.1:8790 --protocol clm --rpm 8
   BCP_SEARCH_OUTPUT=~/econo/bcp/full-search/phase1-smoke bash scripts/bcp_study2/run_search.sh --report-only --measure-flops
   ```
   Compare with Study 2's M0 ranges:
   - peak context 12–25k tokens;
   - 3–6 calls;
   - 0.7–1.9 PFLOPs;
   - about 5,700 tokens per search;
   - FLOP validation valid on every call.
8. **Stop and report:** what matched, what didn't, versions, and timings. Don't start Phase 1.

## 7. Untested

No Dockerfile or compose file is added here. CLM's task images come from `benchmarks/bcp/tasks.py`, which ran unchanged in the cloud container (Linux, Docker Engine). It has not been tested on Docker Desktop.
