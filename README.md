# EconoContext

EconoContext is online declarative context optimization for LLM agents. Between an agent's steps, a predictor answers questions about the agent's context. The predictor is Jev (a classifier) or a set of fixed guesses. A price then decides what to keep, shrink, evict or summarize, and EconoContext applies it to the conversation.

The branch `tier-a-purdue` measures this on an open model: Qwen3.8-27B on Purdue's GenAI Studio. The cost is prefix-reuse FLOPs, following CLM (Shao et al., arXiv 2609.37725, Appendix C). The baselines are CLM's own agent and, following CLM's design, Base and Summary. The earlier Gemini-dollar results are in `docs/results/2026-10-06/`.

Earlier code (v0, the v1 engine, Omnigent/SWE-bench, Claude Code hooks, the Gemini runner) is at tag `econo-jev-final`. A few files written on this branch and removed in step 10's clean-up are at tag `tier-a-step9`. These are the step-1 Purdue scripts and the step-2 restructure map; `docs/cleanup-map.md` lists them.

## Layout

**`econocontext/`** is the method.
- `owner.py` edits the conversation of an agent that owns its context (rules 1–4, 6, 7, 9).
- `session.py` is the run's session database: one empty SQLite file per run, deleted after it.
- `predictor/` holds Jev and the fixed guesses.
- `pricing/lifecycle.py` holds the decision prices.

It uses only the standard library and PyYAML, and imports nothing from the other folders.

**`agents/`** holds our agent that Harbor runs: `react/agent.py`, a small tool-calling agent (bash, read_file, write_file, submit) with the arms raw, jev and prior. With a token budget, raw is CLM's Base and summary is CLM's Summary baseline (Codex-style compaction at 75% of the budget). `react/prompts/` holds prompts taken verbatim from other projects, each with its source and license. The agent reaches models only through `gateway/`.

**`baselines/`** holds the methods we compare against. Each is the authors' code as a pinned git submodule, unmodified, in `baselines/<name>/upstream`, plus our wrapper next to it. `clm/` runs CLM's own agent through our gateway (`clm/run.sh`). CLM's license is CC BY-NC 4.0; we use its code and never copy it into ours. The one exception is its final-turn notice, a single text used verbatim with attribution (`agents/react/prompts/clm_final_turn.txt`), because its wording affects behaviour.

**`gateway/`** is one small OpenAI-compatible proxy (`.venv/bin/python -m gateway.server --provider purdue`). It does five things:
- holds the provider keys;
- paces requests to the provider's limit;
- enforces the caps;
- pins the request fields the measurement depends on;
- logs every call to `runs/gateway/<run_id>/calls.jsonl`.

**`measure/`** measures cost, independently of the method (`.venv/bin/python -m measure.report runs/<label>`):
- model constants from a model's config (CLM Eq. 7);
- a 16-token prefix-cache simulation;
- prefix-reuse FLOPs per call and per run (Eq. 9);
- a prompt's size as the server will count it (`tokens.py`), which agents use to enforce a budget;
- a context profile per task (`profile.py`);
- the per-arm comparison tables (`compare.py`).

**`benchmarks/tblite/`** holds the benchmark (`tasks.py`: the 10 frozen TBLite tasks and their path) and the runner for every arm (`run.py`, all calls through the gateway).

**`scripts/`** holds two scripts:
- `check_secrets.py`, the last gate before every push;
- `purdue_template_probe.py`, which checks the chat-template options Purdue applies.

**`config/`**
- `v2.yaml` holds EconoContext's settings.
- `billing_rates.yaml` holds the price card that EconoContext's decision prices read.

**`tests/`** mirrors the folders above. Run them with `.venv/bin/python -m pytest -q`. No test calls a model.

**`docs/`** holds:
- the results;
- this branch's decisions (`tier-a-decisions.md`);
- the clean-up map (`cleanup-map.md`).

**`runs/`** holds every experiment's logs and results, kept where they were written.

## Environments

- **`.venv`** (Python 3.12): the method, the agents, Harbor and the tests (`pip install -e ".[dev,measure]"`).
- **`.venv-clm`** (Python 3.12): CLM (`pip install -e baselines/clm/upstream`) and transformers, for CLM's agent and the CLM cross-checks.
- **Keys:** they live only in `.env`, which is git-ignored, and only the gateway reads model keys.
