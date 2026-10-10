# EconoContext

EconoContext is online declarative context optimization for LLM agents. Between an agent's steps, a predictor answers questions about the agent's context. The predictor is Jev (a classifier) or a set of fixed guesses. A price then decides what to keep, shrink, evict or summarize, and EconoContext applies it to the conversation.

The branch `tier-a-purdue` measures this on an open model: Qwen3.8-27B on Purdue's GenAI Studio. The cost is prefix-reuse FLOPs, following CLM (Shao et al., arXiv 2609.37725, Appendix C). The earlier Gemini-dollar results are in `docs/results/2026-10-06/`. Their code is at tag `econo-jev-final`.

## Layout

**`econocontext/`** is the method.
- `owner.py` edits the conversation of an agent that owns its context (rules 1–4, 6, 7, 9).
- `session.py` is the run's session database: one empty SQLite file per run, deleted after it.
- `predictor/` holds Jev and the fixed guesses.
- `pricing/lifecycle.py` holds the decision prices.

It uses only the standard library and PyYAML, and imports nothing from the other folders.

**`agents/`** holds the agents that Harbor runs.
- `react/agent.py` is our small tool-calling agent (bash, read_file, write_file, submit), with the arms raw, jev and prior.
- `clm/` (step 6) runs CLM's own agent through our gateway.

Agents reach models only through `gateway/`.

**`gateway/`** is one small OpenAI-compatible proxy (`.venv/bin/python -m gateway.server --provider purdue`). It holds the provider keys, paces requests to the provider's limit, enforces the caps, pins the request fields the measurement depends on, and logs every call to `runs/gateway/<run_id>/calls.jsonl`.

**`measure/`** (step 4) measures cost, independently of the method:
- model constants from a model's config (CLM Eq. 7);
- a 16-token prefix-cache simulation;
- prefix-reuse FLOPs per call and per run (Eq. 9).

**`benchmarks/tblite/`** holds the benchmark (`tasks.py`: the 10 frozen TBLite tasks and their path), the runner for every arm (`run.py`, all calls through the gateway), and two task folders for rule spikes.

**`scripts/`** holds the probes of Purdue's API from step 1.

**`config/`**
- `v2.yaml` holds EconoContext's settings.
- `billing_rates.yaml` holds the price card that EconoContext's decision prices read.

**`third_party/context-language-models/`** is CLM's code as a pinned git submodule, unmodified. Its license is CC BY-NC 4.0; we use it and never copy it into our code.

**`tests/`** mirrors the folders above. Run them with `.venv/bin/python -m pytest -q`. No test calls a model.

**`docs/`** holds the results, this branch's decisions (`tier-a-decisions.md`) and the restructure map.

**`runs/`** holds every experiment's logs and results, kept where they were written.

**`archive/`** holds the paused tracks and their docs (see `archive/README.md`). They don't run in place; tag `econo-jev-final` runs all of them.

## Environments

- **`.venv`** (Python 3.12): the method, the agents, Harbor and the tests (`pip install -e ".[dev]"`).
- **`.venv-clm`** (Python 3.12): CLM (`pip install -e third_party/context-language-models`) and transformers, for the CLM cross-checks.
- **Keys:** they live only in `.env`, which is git-ignored, and only the gateway reads model keys.
