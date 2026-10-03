# EconoCLM v1

CLM ([Context Language Models](https://github.com/facebookresearch/context-language-models),
commit `18dc111`) lets the model manage its own context by editing a mirrored
transcript file. EconoCLM gives a CLM three more things, without changing the model
or CLM's root prompt:

1. **Exact memory**: every command output is saved in full with an ID (`[obs N]`),
   retrievable with `econo get N`, including the parts CLM cut from the context.
2. **Live cache and cost facts**: an `[econo]` status line after every command, and
   an `[econo]` quote after every context edit (what it costs, when it pays off).
3. **Stale-file flags**: "this file changed since you read it".

The experiment compares Raw CLM against EconoCLM on 10 TBLite tasks, with Gemini 3.6 Flash
on Vertex. Both arms go through our measure-only gateway, which is the only source of
cost numbers.

## Layout

| Path | What |
|---|---|
| `core/` | prices, meter, usage normalization, the gateway and its ledger, the per-trial run store, bash-read parser |
| `quote/` | pure functions: the edit quote and the status line |
| `arms/raw_clm/` | config for CLM's unmodified `ClmAgent` |
| `arms/econo_clm/` | `EconoClmAgent` (subclass of `ClmAgent`), its hooks, the `econo` tool and `SKILL.md` |
| `bench/tblite/` | seeded task selection (`tasks.txt`) and the Harbor runner |
| `analysis/` | results table, edit ceiling, quote accuracy |
| `runs/` | outputs (gitignored) |

CLM is CC BY-NC 4.0, so it is imported, never copied.

## Run

```sh
python3.12 -m venv ~/.venv-econoclm && . ~/.venv-econoclm/bin/activate
git clone https://github.com/facebookresearch/context-language-models ../context-language-models
git -C ../context-language-models checkout 18dc111
git clone https://github.com/open-thoughts/OpenThoughts-TBLite ../OpenThoughts-TBLite
pip install -e ../context-language-models -e "econoclm/[test]"
pytest -q econoclm/tests                                   # Gate 1

# Secrets live only in the gateway's environment (default ~/.econoclm/secrets.env, chmod 600):
#   AGENT_PLATFORM_API_KEY=...   ECONOCONTEXT_BASE_URL=https://aiplatform.googleapis.com/v1/projects/<p>/locations/global/endpoints/openapi
D=econoclm/runs/$(date +%F); mkdir -p $D
MAX_SPEND_USD=40 MAX_INFLIGHT=4 python -m econoclm.core.gateway --ledger $D/gateway.sqlite &

python -m econoclm.bench.tblite.run --arms raw --limit 1 --dry-run   # inspect the commands
python -m econoclm.bench.tblite.run --arms raw,econo --reps 1 --workers 4

python -m econoclm.analysis.results_table $D
python -m econoclm.analysis.edit_ceiling $D
python -m econoclm.analysis.quote_check $D
```

## Fairness rules

- The configs are identical except the agent class and `skill_dirs`; `tests/test_run_dry.py` checks this.
- The gateway forwards request bytes unchanged. The only change, in both arms, is that a
  streaming request without usage reporting gets `include_usage` added.
- Rate limits: the gateway resends a call that got 429/503 upstream (the same bytes,
  for at most 240 s), so a quota hiccup cannot crash a trial. The ledger records the
  attempts, the wait, and the time spent queueing; latency counts only the final attempt,
  and wall time is reported both raw and net of those waits.
- Every EconoCLM hook fails open: on an error, CLM's own result is used, and the error
  is counted.
