# Benchmark preparation

Implemented methods are `react` and `econocontext`. Both use the same agent loop, model backend, domain tools, task environment and measurement wrappers. Management prompts/control actions differ and their generated tokens are included. The synthetic fixtures establish protocol correctness only.

## Controlled comparisons

Freeze tasks, initial repository/corpus snapshots, exact model checkpoint, decoding behavior, context capacity, total attempt/output/spending budget across all workers, concurrency, verifier and cache initialization policy. Keep calibration and held-out evaluation separate; freeze profile files before evaluation. Include failed runs in total cost and report known subtotals/completeness. Report verified success, end-to-end wall time, model/tool attempts, token/cache usage, local preparation overhead and prediction error.

Use separate experimental categories:

1. The exact same checkpoint with different context/execution policies, to isolate mechanism differences.
2. Released trained systems/checkpoints, with model/training differences explicitly labeled as reproduction work.

A prompting-only adaptation does not reproduce a trained policy. A fixed-policy delegation ablation is future work to distinguish gains from delegation versus optimization.

## Coding tasks

Load a local repository with an explicit commit, problem statement and instance ID through the task JSON. The adapter creates a separate detached checkout and exports a JSONL prediction containing `instance_id`, `model_name_or_path`, and `model_patch`. It does not download benchmark repositories or hidden tests.

The official evaluator consumes the prediction outside the agent-visible workspace. See the [SWE-bench evaluation guide](https://www.swebench.com/SWE-bench/guides/evaluation/). Required dependencies and container images depend on the chosen task. The bridge is prepared for small pinned repositories; official evaluation and scores are not implemented or claimed. External tasks remain unverified in this MVP.

## Research tasks

The default local text corpus is reproducible and verified deterministically. A task can supply a local directory of `.txt` documents and its own question; external corpora require their own verifier. Live web search and full BrowseComp downloads are outside V1.

## Later integrations

An ACM runner can wrap its existing model/tool calls using the public `Telemetry` interface, as demonstrated by `examples/external_runner.py`. No adoption of the EconoContext planner is required. A future Context-Folding integration would similarly preserve the execution/measurement boundary while explicitly accounting for additional model actions and training differences.

ACM and Context-Folding are not implemented baselines here. Their current checkpoint/environment support must be checked at integration time; this MVP makes no support or reproduction claims about their repositories.
