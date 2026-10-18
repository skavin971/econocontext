# Clean-up map (step 10.0)

Written on 2026-10-11 on `tier-a-purdue` at `a4b4a2c`, before any change.

**Where removed files can still be found:**
- Anything from before Tier A is at tag `econo-jev-final` (`912d7d1`).
- Anything written during Tier A is in this branch's history (see flag f).

**How this map was built:**
- `git ls-files` counts: `runs/tier-a/step10.0-inventory.out`.
- An AST pass over `econocontext/`, `measure/` and `gateway/`, listing definitions that nothing references, each one confirmed with grep: `runs/tier-a/step10.0-unused*.out`.
- A grep of every kept file for the paths removed here.

## Delete (`git rm`)

| Path | Tracked files | Reason | At the tag |
|---|---|---|---|
| `archive/` | 311 | The paused tracks (v0, the v1 engine, Omnigent/SWE-bench, Claude Code hooks, the Gemini runner) and their docs. | yes, at the paths they had before step 2. Three exceptions were written or changed in step 2: `archive/README.md`, `claude_code/tests/test_rules.py` (imports fixed) and `omnigent/test_isolation_omnigent.py`. |
| `runs/v2a`, `v2spike`, `v2spike2`, `v2spike3`, `v2x`, `v2x2` | 119 | The Claude Code (v2) runs. No kept file cites them. | yes |
| `runs/2026-10-05-jev`, `2026-10-05-v2`, `2026-10-05-v2-spike` | 64 | The Claude Code (v2) runs. Cited once, in `docs/results/2026-10-06/README.md:119`; that link now points to the tag (see Edit). | yes |
| `runs/gsmoke`, `gspike`, `gspike2` | 27 | The Gemini smoke run and spikes. Their results are summarised in `runs/2026-10-06-gemini/RESULTS-spike.md`, which is kept. `config/v2.yaml:39` cites gsmoke (see Edit). | yes |
| `runs/.gitignore` | 1 | Ignores Claude Code config files in experiment folders; no kept run has any. | yes |
| `docs/restructure-map.md` | 1 | Step 2's map; that work is done. | no |
| `docs/results/2026-09-30-cost-model-explains-placement.md` | 1 | A v1 result; nothing cites it. | yes |
| `scripts/purdue_smoke.py` | 1 | The step-1 smoke test. Its helpers move into the probe first (flag a). | no |
| `scripts/purdue_token_check.py` | 1 | The step-1 token check; that work is done. | no |
| `benchmarks/tblite/spike-all-nine/`, `spike-own-harness/` | 14 | Spike tasks from the v2 and Gemini tracks. **No test or code uses them**; only old logs mention them. | yes |

## Move (`git mv`)

| From | To | Reason |
|---|---|---|
| `third_party/context-language-models` (submodule, `18dc111`) | `baselines/clm/upstream` | Every baseline goes in `baselines/<name>/upstream` plus our wrapper. `git mv` updates the path in `.gitmodules` (flag d). `.venv-clm` is reinstalled from the new path. |
| `agents/clm/run.sh`, `agents/clm/README.md` | `baselines/clm/run.sh`, `baselines/clm/README.md` | Our wrapper for CLM's agent. `agents/` keeps only `agents/react`. |

## Edit (code nothing uses; comments and links that name removed paths)

| File | Change | Reason |
|---|---|---|
| `econocontext/predictor/jev.py` | remove `Jev.live`, `Jev.subtask` and the `PHASES` import | The owner asks a predictor only `arrival` and `segment` (`owner.py:73, 179, 261`). No test uses them. |
| `econocontext/predictor/prior.py` | remove `Prior.live` and `Prior.subtask` | The same reason. |
| `econocontext/predictor/__init__.py` | remove `PHASES`, plus the `live` and `subtask` lines of the docstring | `PHASES` was used only by `Jev.live`. |
| `econocontext/session.py` | remove `Session.live_big` | Nothing calls it. |
| `econocontext/pricing/lifecycle.py` | remove `Prices.usd` and `placement()` | Nothing calls them. |
| `measure/`, `gateway/` | nothing to remove | The AST pass found no unused definitions. |
| `docs/results/2026-10-06/README.md:119` | point its two `runs/2026-10-05-*` links at GitHub, at the tag | Keeps the citation working. |
| `config/v2.yaml:3`, `:39` | change `archive/claude_code/` and `gsmoke` to "(at tag econo-jev-final)" | Comments that record where these came from. |
| `benchmarks/tblite/tasks.py:6`, `gateway/server.py:59`, `pyproject.toml:11` | change `archive/...` to "tag econo-jev-final" | Comments that name `archive/`. |
| `pyproject.toml` | drop the `docker` marker | It was for the SWE-bench track; no kept test uses it. |
| `.gitignore` | update the path in the CLM comment; drop `.econocontext/` | `.econocontext/` is v1's local store. |
| `tests/unit/test_isolation.py` | scan `agents/` and `baselines/`, skipping `*/upstream/` | `baselines/` replaces `agents/clm`. |
| `benchmarks/tblite/run.py`, `baselines/clm/run.sh`, `baselines/clm/README.md`, `tests/measure/clm_crosscheck.py` | use the new CLM paths | The move. |
| `README.md` | one paragraph per folder, plus "Earlier code (v0, the v1 engine, Omnigent/SWE-bench, Claude Code hooks, the Gemini runner) is at tag `econo-jev-final`." | The new layout. |
| `docs/tier-a-decisions.md` | add step 10's reasons and decisions 1–6; update the paths | Your spec. |

## Keep

- **Runs:**
  - Gemini: `gx1`, `gx1b`, `gx23`, `gx4`, `gx4b`, `2026-10-06-gemini`.
  - Purdue: `purdue-smoke`, `ta1`, `ta2`, `tc1`, `tp1`, `tc9`, `tier-a`, `gateway`, and `tc9s` (flag c).
  - `runs/2026-10-10-jev400`, the Jev probe that `jev.py:19` cites (flag e).
- **Scripts:** `scripts/check_secrets.py` and `scripts/purdue_template_probe.py`.
- **Config:**
  - `config/v2.yaml`, the method's thresholds;
  - `config/billing_rates.yaml`, the price card the method's decision prices read.
- **Docs and lock:** `docs/results/2026-10-06/`, `docs/results/2026-10-11-tblite-arms/` and `requirements.lock`.
- **Old logs:** the logs in `runs/tier-a/*.out` and `runs/2026-10-06-gemini/*.out` that mention removed paths stay as written.

## Untracked files under removed paths (flag b)

These files were never in git, so they are not at the tag either.

| Where | What | Size |
|---|---|---|
| `archive/v0/data/` | v0's data: live-study (19,086 files), artifacts (6,699), workspaces (145), walkthrough (18), `state.sqlite3` | 311 MB |
| `archive/v0/` | 3 design documents (one `.docx`, two `.pptx`), `logs/` (37 `.txt`), and a `.env` | small |
| `runs/v2*`, `2026-10-05-*`, `gsmoke`, `gspike*` | 11 each of `trial.log`, `harbor.log`, `.claude.json`, its backup and `.last-cleanup`; 4 service logs and a shell snapshot | about 250 KB |
| `benchmarks/tblite/spike-*/environment/files/data/big.log` | the spike tasks' input file | 2 × 160 KB |
| `archive/**` | caches: `.pyc`, `.pytest_cache`, `egg-info` | small |

**Proposal:** move everything except the caches to `~/EconoContext-untracked/`, keeping the same relative paths, and delete the caches. The `.env` is moved without being opened.

## Flags for your approval

- **a. The probe needs helpers from the smoke script.** `purdue_template_probe.py` imports `BASE`, `MODEL`, `PROMPT`, `ROOT`, `TOOLS`, `patient`, `read_key` and `save` from `purdue_smoke.py`.
  - **Proposal:** copy those 8 definitions into the probe word for word, then delete `purdue_smoke.py`.
  - The probe stays the one script outside `gateway/` that reads the key, as it was before.
- **b. Untracked files:** move them (table above), don't delete them.
- **c. `runs/tc9s` stays,** although it isn't on your keep list. It is the step-9 smoke run on Purdue, and `runs/tier-a/step9a-report.md` and `step9-progress.md` cite it.
- **d. The submodule's name stays.** `git mv` updates the submodule's path in `.gitmodules`, but its name stays `third_party/context-language-models`. Renaming it would mean editing `.git/modules` by hand.
- **e. `runs/2026-10-10-jev400` is cited as evidence.** `jev.py:19` cites it as the measurement behind characters per Jev token (2.55–2.67 measured; 2.3 from 10a).
  - **Proposal:** keep it (2 files, 8 KB).
- **f. Some removed files are not at `econo-jev-final`.** They were written during Tier A, so they exist only in this branch's history:
  - `docs/restructure-map.md`;
  - `scripts/purdue_smoke.py` and `purdue_token_check.py`;
  - `archive/README.md` and the two archived test files changed in step 2;
  - `runs/2026-10-10-jev400`, if it is removed.

  **Proposal:** tag `tier-a-step9` at `a4b4a2c`, the last commit before the clean-up, and push it with the branch in 10a. The README would then name both tags.

## Applied (2026-10-11)

You approved flags a–f as proposed, and the map was applied in the step-10.0 commit with `git rm` and `git mv`, plus the edits listed above.
- **Flag a:** the probe also needed `request()`, which `patient()` calls, so 9 definitions were copied word for word. The probe still judges its saved probes offline, with the same verdicts as in step 1: `runs/tier-a/step10.0-checks.out`.
- **Flag b:** 26,016 untracked files were moved to `~/EconoContext-untracked/` and 193 cache files were deleted: `runs/tier-a/step10.0-untracked.out`.
- **Flag f:** tag `tier-a-step9` was created locally at `a4b4a2c`. It is pushed with the branch in 10a.
- **Unused code:** after the removals, the AST pass finds nothing new (`runs/tier-a/step10.0-unused-after.out`). Its only hits are `Jev.segment` and `Prior.segment`, which the owner calls by name (`getattr(self.predictor, "segment")`).
- **Everything was removed cleanly.**
