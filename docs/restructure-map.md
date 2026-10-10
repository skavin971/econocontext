# Restructure move map (step 2, branch tier-a-purdue)

**Approved by Kavin on 2026-10-10, with changes, and carried out in step 2b.** This file now records what was done:
- `decide.py` is archived; its `TESTS` regex moved verbatim into `session.py`.
- Five lifecycle tests moved, not four (see Flag 3).
- A fresh lock for `.venv` is generated at the end of step 4.

Built from the AST import graph of every tracked Python file (`runs/tier-a/step2-import-graph.out`). The own-harness path is closed. It is our agent plus `econocontext/owner.py` and what they import:
- `econocontext/{__init__, owner, session, tokens, decide}.py`
- `predictor/{__init__, jev, prior}.py`
- `pricing/{__init__, lifecycle}.py`
- the data files `config/v2.yaml` and `config/billing_rates.yaml`

None of these imports anything outside the path. Moves are `git mv` only. The only edits are the import paths, the Harbor agent path strings, and the pyproject package lists that the moves require. The old state stays reproducible at tag `econo-jev-final` (912d7d1).

Format: `old → new`, then the reason. "stays" means unchanged.

## Kept in place

- `.gitignore`, `.gitmodules`, `third_party/context-language-models` → stay. Repo plumbing; CLM submodule from step 0.
- `config/v2.yaml` → stays. `owner.py` reads its `harness` and `jev` sections.
- `config/billing_rates.yaml` → stays. **Flag 1.**
- `docs/results/` → stays. Results history.
- `econocontext/__init__.py`, `owner.py`, `session.py`, `tokens.py` → stay. The method.
- `econocontext/decide.py` → `archive/claude_code/econocontext/decide.py`. **Flag 2:** its `TESTS` regex moved verbatim next to `read_only_bash` in `econocontext/session.py`, and `owner.py` imports it from there.
- `econocontext/predictor/` (`__init__`, `jev`, `prior`) → stays. Jev and the fixed guesses.
- `econocontext/pricing/__init__.py`, `pricing/lifecycle.py` → stay. The decision prices.
- `benchmarks/tblite/spike-all-nine/`, `benchmarks/tblite/spike-own-harness/` → stay. Harbor task folders for the rule spikes.
- `scripts/` (`purdue_smoke`, `purdue_token_check`, `purdue_template_probe`) → stays. The step-1 probes.
- `runs/` → stays. Every earlier result, as agreed.
- `tests/__init__.py`, `tests/unit/__init__.py` → stay.
- `tests/unit/test_isolation.py` → stays. Extended in 2c; its Omnigent-only rule moves to `archive/omnigent/test_isolation_omnigent.py`.
- `pyproject.toml` → stays, with two edits:
  - the `include` list becomes `["econocontext*", "agents*"]` (`gateway*` and `measure*` are added in steps 3 and 4, when those packages exist);
  - the `econocontext = ["store/schema.sql"]` package-data line is removed, because `store/` is archived.

## Into the new layout

- `adapters/harbor/econo_agent.py` → `agents/react/agent.py`. Our Harbor agent; the Harbor path becomes `agents.react.agent:EconoAgent`.
- `adapters/__init__.py` → `agents/__init__.py`. Package marker (empty).
- `adapters/harbor/__init__.py` → `agents/react/__init__.py`. Package marker.
- `tests/v2/test_owner.py` → `tests/econocontext/test_owner.py`. Tests mirror the folders.
- `tests/v2/test_econo_agent.py` → `tests/agents/test_react_agent.py`. Its import becomes `agents.react.agent`.
- `tests/v2/test_rules.py`: its 5 tests that use only `lifecycle` → new `tests/econocontext/test_prices.py`, verbatim. **Flag 3.**
- New, empty: `tests/econocontext/__init__.py` and `tests/agents/__init__.py`, so the test folders can't shadow the real `econocontext` and `agents` packages.

## archive/claude_code/: the Claude Code hooks track (paused 2026-10-06)

- `adapters/harbor/claude_code_econo.py` → `archive/claude_code/claude_code_econo.py`. Hook driver.
- `econocontext/service.py` → `archive/claude_code/econocontext/service.py`. Hook service.
- `econocontext/transcript.py` → `archive/claude_code/econocontext/transcript.py`. Claude Code transcript reader, used only by the service and the runner.
- `benchmarks/tblite/run.py` → `archive/claude_code/benchmarks/run.py`. The Claude Code runner. Its `FROZEN` and `TBLITE` constants are copied into the new runner in step 3.
- `benchmarks/tblite/evidence.py` → `archive/claude_code/benchmarks/evidence.py`. Evidence table for the hook runs.
- `tests/v2/test_rules.py`, minus the 5 lifecycle tests → `archive/claude_code/tests/test_rules.py` (22 tests). It tests `decide.py`'s hook rules through `service.py`.

## archive/gemini/: the 2026-10-06 Gemini dollar-cost experiment

- `benchmarks/tblite/run_gemini.py` → `archive/gemini/run_gemini.py`. Dollar budget runner (paused: no dollars in the new path).
- `benchmarks/tblite/compare_gemini.py` → `archive/gemini/compare_gemini.py`. Comparison tables.
- `benchmarks/tblite/verify_gemini.py` → `archive/gemini/verify_gemini.py`. Dollar verification.

## archive/omnigent/: the Omnigent and SWE-bench track

- `omnigent_layer/` (whole, including its 78 tests) → `archive/omnigent/omnigent_layer/`. The old gateway, replaced by `gateway/` in step 3. The editable install in `.venv` will point at a missing path; nothing kept imports it.
- `harness/` (whole) → `archive/omnigent/harness/`. Omnigent session runner and specs.
- `benchmarks/swebench/` → `archive/omnigent/benchmarks/swebench/`. The SWE-bench benchmark.

## archive/econocontext_v1/: the v1 engine (off the own-harness path)

- `econocontext/{assembler, costmodel, guard, learn, monitor, optimizer, oracle, planner, store}/` → `archive/econocontext_v1/econocontext/…`. The v1 engine.
- `econocontext/{engine, host, types, config, evidence}.py` → `archive/econocontext_v1/econocontext/`. Same.
- `econocontext/pricing/{cost_model, ledger, predictor, rates}.py` → `archive/econocontext_v1/econocontext/pricing/`. The v1 dollar ledger and cost model.
- `config/econocontext.yaml` → `archive/econocontext_v1/config/`. The v1 engine config.
- `tests/unit/conftest.py` and the 17 v1 unit test files → `archive/econocontext_v1/tests/unit/`. Its conftest imports the v1 engine.
- `tests/test_planner_jev.py` → `archive/econocontext_v1/tests/`. v1 planner.

## archive/v0/ and archive/docs/

- `v0/` (whole) → `archive/v0/`. The first prototype.
- `README.md` → `archive/README-old.md`. Replaced by a short new README (2d).
- `PLAN.md` → `archive/docs/PLAN.md`. The v1 plan.
- `AGENT_DB_CONTRACT.md` → `archive/docs/AGENT_DB_CONTRACT-top-level.md`. It differs from the `docs/` copy.
- `docs/{1-6, AGENT_DB_CONTRACT, COST_TRACKING, README, TESTING, gemini-measurement-schema, harness-baseline, omnigent-findings}.md` and the 11 tracked `docs/html/*` → `archive/docs/`. **Flag 4.**
- `requirements.lock` → `archive/requirements.lock`. **Flag 5.**
- New: `archive/README.md`, a few lines: what each folder is, and that tag `econo-jev-final` runs all of it. Archived code is not expected to import or run in place.

## Untracked, untouched

`data/`, `logs/`, `notes/`, `bench/` (only `__pycache__`), `.claude/`, `docs/html/econocontext-{overview,progress}.html`, `econocontext.egg-info/`, `.venv/`, `.venv-clm/`.

## Flags for Kavin

1. **`config/billing_rates.yaml` stays in `config/`.** EconoContext's decision prices (`pricing/lifecycle.from_card`) read its Vertex Gemini card. That is the method's pricing, not a dollar measurement, and it is out of scope to change. Moving it would break the method.
2. **`econocontext/decide.py` is archived** (your change). Its `TESTS` regex moved verbatim into `session.py`, next to `read_only_bash`, and `owner.py` imports it from there.
3. **`test_rules.py` is split.** Its tests that use only `lifecycle` and the price card move verbatim to `tests/econocontext/test_prices.py`. There are 5, not 4: `test_measured_compaction_cost_makes_compaction_rare` sat under the file's "service" heading but uses only `lifecycle`. The other 22 tests cover the Claude Code hook rules through `service.py` and are archived with it.
4. **The v1 docs** (`docs/1-6` and friends, plus their HTML) describe the v1 engine, so they move to `archive/docs/`. `docs/results/` stays.
5. **`requirements.lock`** pins the v1 environment of 2026-09-28. It is archived, and a fresh lock for `.venv` is generated at the end of step 4.
6. **Tests after the move:** the active suite is 34 tests:
   - `test_owner`: 19 (17 functions, one parametrized);
   - `test_react_agent`: 7;
   - `test_prices`: 5;
   - `test_isolation`: 3 (the existing core rule plus the 2 new rules).

   The other 135 of the baseline's 167 move with the archived code and are not run: `test_rules` 22, `test_planner_jev` 27, v1 unit tests 85, and the Omnigent isolation rule 1. So do the 78 `omnigent_layer` tests. All of them still run at tag `econo-jev-final`.
7. **Isolation rules (2c), as you decided:**
   - `measure/` never imports `econocontext`.
   - Files under `agents/` name no model-provider host or key variable; they reach models only through `gateway/`.
   - `scripts/` is outside that rule.
