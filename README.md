# EconoContext

EconoContext is online declarative context optimization for LLM agents. Between an agent's steps, a predictor answers questions about the agent's context. The predictor is Jev (a classifier) or a set of fixed guesses. A price then decides what to keep, shrink, evict or summarize, and EconoContext applies it to the conversation.

**Lab mates checking our PFLOPs math with CLM: start at [`pflop_accuracy/README.md`](pflop_accuracy/README.md).** That folder runs only CLM's agent and our measurement code; you need nothing else in this repository.

Tier A measures this on an open model: Qwen3.8-27B on Purdue's GenAI Studio. The cost is prefix-reuse FLOPs, following CLM (Shao et al., arXiv 2609.37725, Appendix C). The baselines are CLM's own agent and, following CLM's design, Base and Summary. The earlier Gemini-dollar results are in `docs/results/2026-10-06/`.

Earlier code (v0, the v1 engine, Omnigent/SWE-bench, Claude Code hooks, the Gemini runner) is at tag `econo-jev-final`. A few files written on this branch and removed in step 10's clean-up are at tag `tier-a-step9`. These are the step-1 Purdue scripts and the step-2 restructure map; `docs/cleanup-map.md` lists them.

## Layout

**`econocontext/`** is the method.
- `owner.py` edits the conversation of an agent that owns its context (rules 1–4, 6, 7, 9). Its state is in the agent DB (`AgentDB/`, step 12a): every message is a segment, the window holds each one's position and form with its exact token count, and tool results are stored with the workspace version they read. Jev's input is built only from these reads plus the task. Each decision is a line of `decisions.jsonl`, beside the trial's `trajectory.json`.
- `commands.py` says which tool calls only read, and gives a call's identity.
- `predictor/` holds Jev and the fixed guesses.
- `pricing/lifecycle.py` holds the decision prices.
- `policy.py` runs EconoContext as a policy of our harness (`Econo`), with the agent DB at `AGENTDB_DSN`; `Directed` is a stub that refuses to run.

It uses the standard library, PyYAML, the agent DB (`agent_db`) and one function of `measure/` (`count_text`, the served model's tokenizer, so the method and the measure count tokens the same way). It imports nothing from `harness/`, `baselines/` or `gateway/`.

**`AgentDB/`** is the agent DB, a trimmed copy of the upstream Postgres store (`agent_db`; `AgentDB/CHANGES.md` lists every change). Real runs use Postgres 16 in Docker (`docker compose up -d`, `docker-compose.yml`); tests start their own Postgres from pip (pgserver). Only `econocontext/` uses it; it holds no cost.

**`harness/`** is our own harness, the Harbor agent `harness.agent:Agent` (step 11): one tool-calling loop (bash, read_file, write_file, submit) for every policy. `interface.py` is what the model sees, the same for every policy (`ours`, or `legacy` to reproduce the runs of steps 9 and 10); `budget.py` is CLM's budget gate; `tools.py` runs the tools in the task's container. A policy, picked with the runner's `--policy` and imported by name, is the only part that differs between arms: `baselines/base`, `baselines/summary` or `econocontext/policy.py`. The harness reaches models only through `gateway/`.

**`baselines/`** holds the methods we compare against. `base/` (CLM's Base: no context management) and `summary/` (CLM's Summary: Codex-style compaction at 75% of the budget, with Codex's prompts, Apache-2.0) are policies of our harness. Other people's agents are the authors' code as a pinned git submodule, unmodified, in `baselines/<name>/upstream`, plus our wrapper next to it: `clm/` runs CLM's own agent through our gateway (`clm/run.sh`). CLM's license is CC BY-NC 4.0; we use its code and never copy it into ours. The one exception is its final-turn notice, a single text used verbatim with attribution (`harness/prompts/clm_final_turn.txt`), because its wording affects behaviour.

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
- a prompt's size as the server will count it (`tokens.py`), which the harness uses to enforce a budget, and a text's size by the same tokenizer (`count_text`), which EconoContext uses for every token count;
- a context profile per task (`profile.py`);
- the per-arm comparison tables (`compare.py`).

**`benchmarks/tblite/`** holds the benchmark (`tasks.py`: the 10 frozen TBLite tasks and their path) and the runner for every arm (`run.py`, all calls through the gateway).

**`scripts/`** holds two scripts:
- `check_secrets.py`, the last gate before every push;
- `purdue_template_probe.py`, which checks the chat-template options Purdue applies.

**`config/`**
- `v2.yaml` holds EconoContext's settings.
- `billing_rates.yaml` holds the price card that EconoContext's decision prices read.

**`tests/`** mirrors the folders above (and `AgentDB/tests` is run with them). Run them with `.venv/bin/python -m pytest -q`. No test calls a model.

**`docs/`** holds:
- the results;
- this branch's decisions (`tier-a-decisions.md`);
- the clean-up map (`cleanup-map.md`).

**`runs/`** holds every experiment's logs and results, kept where they were written.

## Environments

- **`.venv`** (Python 3.12): the method, the harness, Harbor and the tests (`pip install -e ".[dev,measure,agentdb]" -e AgentDB`).
- **`.venv-clm`** (Python 3.12): CLM (`pip install -e baselines/clm/upstream`) and transformers, for CLM's agent and the CLM cross-checks.
- **Keys:** they live only in `.env`, which is git-ignored, and only the gateway reads model keys.
- **The agent DB:** `docker compose up -d`, and `AGENTDB_DSN` in `.env` (git-ignored) points to it.
