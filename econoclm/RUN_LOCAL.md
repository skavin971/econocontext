# Running Gates 2–6 on your machine

This file gives every command for the live part of the experiment: the gateway smoke
test, the task health check, both pilots, and both full arms. It works on Linux or macOS
with Docker. Gate 1 (the unit tests) already passed in the cloud session; you rerun it
here in step 3 as a setup check.

Each phase writes to its own folder under `econoclm/runs/` (gitignored), with its own
gateway ledger. The $40 spend cap still covers the whole experiment, because the gateway
adds up every `runs/*/gateway.sqlite` when it starts.

| Phase | Folder (`$D` = date, e.g. `2026-10-04`) |
|---|---|
| Gate 2 smoke | `runs/$D-g2-smoke` |
| Health check (no model) | `runs/$D-health` |
| Gate 3 Raw pilot | `runs/$D-g3-pilot-raw` |
| Gate 4 Raw, 10 tasks + Gate 6 EconoCLM, 10 tasks | `runs/$D-main` (both arms in one folder, so one results table compares them) |
| Gate 5 EconoCLM pilot | `runs/$D-g5-pilot-econo` |

On the MacBook Air M2 used for v1, both arms run with `--workers 2` and `MAX_INFLIGHT=2`
(recorded in REPORT.md §1). Keep the same number for both arms.

---

## 1. Requirements

- **Docker** running (`docker ps` works without sudo).
  - Linux x86_64 is the safest host.
  - On Apple Silicon, TBLite images are built for x86_64. If the health check (step 6) shows build failures, set `export DOCKER_DEFAULT_PLATFORM=linux/amd64` before every command below, so both arms get it.
- **Python 3.12**: `python3.12 --version`. On macOS, install it with `brew install python@3.12`.
- **git**, and about 20 GB of free disk space for task images.

## 2. Pinned clones, side by side

```sh
mkdir -p ~/econo && cd ~/econo
git clone https://github.com/skavin971/econocontext && git -C econocontext checkout econoclm
git clone https://github.com/facebookresearch/context-language-models
git -C context-language-models checkout 18dc111
git clone https://github.com/open-thoughts/OpenThoughts-TBLite
git -C OpenThoughts-TBLite checkout 5c37b41
```

The runner expects exactly this layout: `econocontext/`, `context-language-models/` and
`OpenThoughts-TBLite/` next to each other. Elsewhere, pass `--clm-repo` and `--tblite`.

## 3. Python 3.12 venv and installs (Gate 1 again, as a setup check)

```sh
cd ~/econo/econocontext
python3.12 -m venv .venv-econoclm && . .venv-econoclm/bin/activate
pip install -U pip
pip install -e ../context-language-models        # brings harbor==0.16.1, litellm, tiktoken
pip install -e "econoclm/[test]"
harbor --version                                 # 0.16.1
pytest -q econoclm/tests                         # all must pass; Docker tests use alpine and python:3.12-slim
```

Keep this venv active for every later step.

## 4. The tokenizer (same file as the cloud session)

CLM counts tokens with tiktoken `o200k_base`. Instead of downloading the vocab, every
EconoCLM entry point (runner, tests, analysis) sets `TIKTOKEN_CACHE_DIR` to the copy
bundled with litellm, unless you already set it. tiktoken checks that file's hash. So
both arms, and both machines, use the identical tokenizer.

Check it:

```sh
python -c "from econoclm.core.tokenizer import use_bundled_tokenizer as u; u(); \
from clm_harness.utils import tokens as tk; print(tk.count_tokens([{'role':'user','content':'hi there'}]))"
# expect: (2, 'tiktoken:o200k_base')    — 'approx_chars4' means it is NOT working
```

## 5. Secrets file

Only the gateway reads it. Agents only ever get `OPENAI_API_KEY=placeholder`.

```sh
mkdir -p ~/.econoclm && chmod 700 ~/.econoclm
cat > ~/.econoclm/secrets.env <<'EOF'
AGENT_PLATFORM_API_KEY=<your NEW Vertex key>
ECONOCONTEXT_BASE_URL=https://aiplatform.googleapis.com/v1/projects/<your-project-id>/locations/global/endpoints/openapi
EOF
chmod 600 ~/.econoclm/secrets.env
```

- The key goes upstream as the `x-goog-api-key` header.
- Use the **global** endpoint: the prices in `core/prices.py` are global-endpoint prices.
- To keep the file elsewhere, set `ECONOCLM_SECRETS=/path/to/file`.

## 6. Starting and stopping the gateway (once per phase)

```sh
D=$(date +%F)                 # keep the same $D for the whole experiment
P=$D-g2-smoke                 # the phase folder (see the table at the top)
mkdir -p econoclm/runs/$P
MAX_SPEND_USD=40 MAX_INFLIGHT=2 \
  python -m econoclm.core.gateway --ledger econoclm/runs/$P/gateway.sqlite --port 8787 \
  > econoclm/runs/$P/gateway.log 2>&1 &
sleep 1; head -1 econoclm/runs/$P/gateway.log
# econoclm gateway on http://127.0.0.1:8787 -> https://...  (spend cap $40.00, already spent in other phases $0.0000, max in flight 2)
```

To stop it before the next phase: `pkill -f econoclm.core.gateway`. Always restart it
with the next phase's `--ledger`. If the ledger and the run folder don't match, `run.py`
refuses to start.

## 7. Gate 2: smoke test (one real call, well under one cent)

```sh
python -m econoclm.bench.smoke --ledger econoclm/runs/$P/gateway.sqlite
```

What to check:

- It ends with `GATE 2: PASS`. That means HTTP 200, every usage column filled, and `cost_usd > 0`.
- `reply model` should be Gemini 3.6 Flash. If Vertex answers with a different model name or version, stop and tell me.
- The two usage lines answer the open question about Vertex's field names: is `prompt_tokens_details` present, and do `completion_tokens_details.reasoning_tokens` appear?
  - A 4-token prompt is below Gemini's caching minimum, so `cached_tokens` = 0 is expected here.
  - The details field may be absent entirely; the gateway records that as 0.

Then stop the gateway.

## 8. Task health check (no model calls, no gateway needed)

Each of the 10 tasks runs once with its own reference solution (Harbor's `oracle` agent).
A task that fails is swapped for the next one in the seeded order.

```sh
python -m econoclm.bench.tblite.health_check --date $D-health --dry-run   # the 10 commands
python -m econoclm.bench.tblite.health_check --date $D-health --workers 2
```

- The summary goes to `runs/$D-health/health.md`.
- Per-task logs go to `runs/$D-health/health_logs/`.
- Harbor's trials go to `runs/$D-health/health/`.

If any task failed, look at its log, then apply the swaps and commit:

```sh
python -m econoclm.bench.tblite.health_check --date $D-health --write
git add econoclm/bench/tblite/tasks.txt && git commit -m "econoclm: tasks.txt after health check"
```

`--write` reruns the checks and then replaces `tasks.txt`. A task the oracle can't solve
would count as a failure in both arms and only add noise.

## 9. Gate 3: Raw CLM pilot (first task only)

Start the gateway with `P=$D-g3-pilot-raw` (step 6), then:

```sh
python -m econoclm.bench.tblite.run --arms raw --limit 1 --date $D-g3-pilot-raw --dry-run
python -m econoclm.bench.tblite.run --arms raw --limit 1 --workers 1 --date $D-g3-pilot-raw
python -m econoclm.analysis.gate_check econoclm/runs/$D-g3-pilot-raw --gate 3
```

Outputs:

- `runs/$D-g3-pilot-raw/raw-<task>-r1/`: `command.txt`, which is the exact Harbor command, and `harbor.log`.
- `runs/$D-g3-pilot-raw/harbor/raw-<task>-r1/`:
  - `result.json` (reward, timing);
  - `agent/` with CLM's logs: `usage.json`, `timing.json`, `trajectory.json`, `context_snapshots/`.

What to check (`gate_check` prints each as PASS/FAIL):

- gateway calls = CLM LM calls;
- a Harbor reward exists;
- no call has `finish_reason = length`.

**If any call was cut by length** (the one allowed fallback):

1. Set `max_tokens: 8192` in **both** `arms/raw_clm/config.yaml` and `arms/econo_clm/config.yaml`.
2. Run `pytest -q econoclm/tests/test_run_dry.py`.
3. Commit.
4. Rerun this gate with `--date $D-g3-pilot-raw-8192`, starting the gateway with that folder.
5. Report both pilots.

Also glance at the gateway's rate-limit columns:

```sh
sqlite3 econoclm/runs/$D-g3-pilot-raw/gateway.sqlite \
  "SELECT COUNT(*), SUM(upstream_attempts-1), ROUND(SUM(ratelimit_wait_ms)/1000,1), SUM(http_status!=200) FROM calls"
```

## 10. Gate 4: Raw CLM, all 10 tasks (1 rep, 2 in parallel)

Start the gateway with `P=$D-main`, then:

```sh
python -m econoclm.bench.tblite.run --arms raw --reps 1 --workers 2 --date $D-main
python -m econoclm.analysis.results_table econoclm/runs/$D-main --arms raw
python -m econoclm.analysis.edit_ceiling  econoclm/runs/$D-main --arms raw
```

These write `results.md`, `results.csv` and `edit_ceiling.md` in `runs/$D-main/`. What to look at:

- **Tasks passed and $.**
- **`infra_fail`** must be 0. Non-zero means a trial died on rate limits or an upstream error, not on the agent. Tell me before continuing.
- **Upstream retries and rate-limit wait.** Compare wall time with "wall time net of rate-limit waits".
- **The edit ceiling**, and its split into *format change* (CLM rewriting tool turns as text) and *edit position* (re-reads from the turn the model actually edited).

## 11. Gate 5: EconoCLM pilot (first task only)

Stop the gateway. Restart it with `P=$D-g5-pilot-econo`, then:

```sh
python -m econoclm.bench.tblite.run --arms econo --limit 1 --workers 1 --date $D-g5-pilot-econo
python -m econoclm.analysis.gate_check econoclm/runs/$D-g5-pilot-econo --gate 5
python -m econoclm.analysis.quote_check econoclm/runs/$D-g5-pilot-econo
```

Extra outputs: `runs/$D-g5-pilot-econo/econo-<task>-r1/` holds
- `econo.sqlite` (observations, files, edits, status lines, hook errors, the model's `econo` use, `get_checks`);
- `obs/` (every full output);
- `log.tsv`;
- `summary.json`.

`gate_check --gate 5` checks:

- the three Gate 3 checks;
- saved outputs = commands run;
- `econo get` is byte-identical inside the sandbox for 3 random IDs. This is checked by sha1 at the end of the run, after the model's log was collected, so the check isn't counted as model use;
- hook errors = 0;
- `quote_check` runs.

It also prints how often the model used `econo` and 3 example `[econo]` lines. Send me that output.

## 12. Gate 6: EconoCLM, all 10 tasks

Restart the gateway with `P=$D-main`, the **same** folder as Gate 4. Then:

```sh
python -m econoclm.bench.tblite.run --arms econo --reps 1 --workers 2 --date $D-main
python -m econoclm.analysis.results_table econoclm/runs/$D-main
python -m econoclm.analysis.edit_ceiling  econoclm/runs/$D-main
python -m econoclm.analysis.quote_check   econoclm/runs/$D-main
```

Then fill in `econoclm/REPORT.md` (sections 2–7) from `results.md`, `edit_ceiling.md`
and `quote_check.md`, or send me the folder and I'll write it.

The two arms run at different times (Gates 4 and 6), so quota conditions can differ. The
rate-limit columns show whether they did.

## 13. What to send back

- Per gate: the `gate_check`, `smoke` or analysis output. For Gate 4 and Gate 6: the `.md` files.
- The whole `runs/$D-*` folders, zipped, if you want me to dig in. They hold full prompts and outputs but no key.
- **Never** send `~/.econoclm/secrets.env`.

## Notes

- The Docker certificate workaround discussed in the cloud session is **not** needed here. It was only for that session's TLS-intercepting proxy.
- Spend cap: the gateway refuses new calls (HTTP 429, "SPEND CAP") once all ledgers together pass `MAX_SPEND_USD`. `run.py` also stops launching trials. Ask me before raising it.
- A trial still running when the cap trips will fail after CLM's own retries. It shows up as an exception in the table.
