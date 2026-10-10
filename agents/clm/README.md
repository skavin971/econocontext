# CLM's agent, through our gateway

`run.sh` runs one Harbor trial of CLM's own agent (`clm-minimal`, i.e. `clm_harness.clm_agent.harness:ClmAgent`) on a TBLite task.
- **The code** comes from the pinned submodule `third_party/context-language-models` (license CC BY-NC 4.0). It is used, never copied into our code.
- **Environment:** it runs from `.venv-clm` (Python 3.12, CLM installed editable).
- **Model calls** go only to the `api_base` it is given: our gateway. So they land in `runs/gateway/<run_id>/calls.jsonl` like every other agent's.

```
.venv/bin/python -m gateway.server --provider purdue        # in another terminal
agents/clm/run.sh tc1 acl-permissions-inheritance 32000      # label, task, context budget, [repeat]
```

**What it passes to CLM's agent**
- `api_base`: the gateway's route for this run.
- `context_budget_tokens`: CLM's context budget. A small budget forces the model to edit its context file.
- `cost_metric=flops`, `flops_model_key=27b`, `flops_n_body=C_token/2` and `flops_tokenizer=<the measured model>`, so CLM's own FLOPs block (attached to `trajectory.ctx.json`) uses our constants.

Everything else is CLM's default: 64 steps, `max_tokens` 16384, temperature 0.7, `top_p` 0.95, and thinking on.

**Outputs**
- `runs/<label>/clm-b<budget>/<task>-r<n>/` holds `command.txt`, `run.json` and `harbor.out`.
- The Harbor trial holds CLM's `trajectory.json`, `trajectory.ctx.json` (ATIF-CTX, with CLM's FLOPs) and `usage.json`. The usage file has the edit counters `n_ctx_syncs`, `n_ctx_syncs_real`, `n_ctx_grew` and `n_ctx_rejected`.
- `tests/measure/clm_three_ways.py` computes the run's FLOPs three ways (step 6).
