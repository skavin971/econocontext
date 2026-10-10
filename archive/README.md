# Archive

These are the paused tracks and the old docs, moved here on 2026-10-10 (branch `tier-a-purdue`, step 2; map in `docs/restructure-map.md`).

Nothing here runs in place, because the imports point at the old layout. Tag `econo-jev-final` (912d7d1) has everything in its original place, with all tests passing (167, plus 78 in `omnigent_layer`).

- **`claude_code/`**: EconoContext inside Claude Code through its hooks (paused 2026-10-06). It holds:
  - the hook driver (`claude_code_econo.py`);
  - the hook service and its rules (`econocontext/service.py`, `decide.py`);
  - the transcript reader;
  - the TBLite runner (`benchmarks/run.py`) and the evidence table;
  - the hook-rule tests.
- **`gemini/`**: the 2026-10-06 Gemini experiment's runner, comparison and dollar verification.
- **`omnigent/`**: the Omnigent and SWE-bench track. It holds the old gateway (`omnigent_layer/`, with its own tests), the session runner (`harness/`) and the SWE-bench benchmark.
- **`econocontext_v1/`**: the v1 engine (planner, optimizer, assembler, store and the rest), with its config and tests.
- **`v0/`**: the first prototype.
- **`docs/`**: the v1 documentation and its HTML, the old plan, and the agent-database contract.
- **`README-old.md`**: the previous top-level README.
- **`requirements.lock`**: pinned the v1 environment (2026-09-28). A fresh lock for `.venv` follows in step 4.
