# REPORT_QWEN: Qwen3.6-27B on 10 TBLite tasks

## Flags

- **CLM: replay and server-count FLOPs differ by -19.0% (more than 5%).**
- **CLM: CLM's FLOPs source was ['hf:Qwen/Qwen3.6-27B', 'server_usage_cached'] for some runs, not server_usage_cached (the server did not report cached tokens on every call).**

## Per arm

| Arm | Runs | Pass rate | PFLOPs (replay) | PFLOPs (server counts) | Replay − server | PFLOPs per solved task (replay) | Prompt tokens computed / reused (replay) | Generated | Calls | Edits | Rollbacks | Calls at max_tokens | Peak prompt | econo use | View edits |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CLM | 10 | 3/10 | 15.67 | 19.35 | -19.0% | 5.22 | 170,761 / 1,468,416 | 131,330 | 203 | 0 | 0 | 45 | 19,175 | – | – |

## Per task (mean, min–max over runs)

| Task | Arm | Pass rate | PFLOPs (replay) | PFLOPs (server) | Calls | Edits | Peak prompt |
|---|---|---|---|---|---|---|---|
| acl-permissions-inheritance | CLM | 0/1 | 0.273 | 0.366 | 5 | 0 | 3.82e+03 |
| anomaly-detection-ranking | CLM | 1/1 | 1.05 | 1.19 | 12 | 0 | 1.26e+04 |
| api-endpoint-permission-canonicalizer | CLM | 0/1 | 4.26 | 5.58 | 64 | 0 | 1.53e+04 |
| bandit-delayed-feedback | CLM | 0/1 | 0.273 | 0.263 | 4 | 0 | 5.07e+03 |
| chained-forensic-extraction_20260101_011957 | CLM | 0/1 | 1.75 | 1.94 | 11 | 0 | 1.78e+04 |
| malicious-package-forensics | CLM | 0/1 | 1.26 | 1.15 | 7 | 0 | 1.92e+04 |
| maven-slf4j-conflict | CLM | 1/1 | 2.34 | 3.1 | 18 | 0 | 1.84e+04 |
| pandas-etl | CLM | 1/1 | 0.431 | 0.557 | 9 | 0 | 6.7e+03 |
| sales-data-csv-analysis | CLM | 0/1 | 0.75 | 0.748 | 9 | 0 | 4.26e+03 |
| scan-linux-persistence-artifacts | CLM | 0/1 | 3.28 | 4.44 | 64 | 0 | 1.08e+04 |

## System prompts

- CLM: 10/10 runs carry the expected system prompt; prompt hashes: 7ed71b99ce14

## Setup (versions and settings)

| Item | Value |
|---|---|
| vLLM | 0.30.0 (live: 0.30.0) |
| Model | Qwen/Qwen3.6-27B @ 6a9e13bd6fc8f0983b9b99948120bc37f49c13e9 (served as qwen36-27b) |
| Server flags | `--revision 6a9e13bd6fc8f0983b9b99948120bc37f49c13e9 --tokenizer-revision 6a9e13bd6fc8f0983b9b99948120bc37f49c13e9 --served-model-name qwen36-27b --dtype bfloat16 --tensor-parallel-size 1 --max-model-len 65536 --gpu-memory-utilization 0.92 --max-num-seqs 64 --enable-prefix-caching --enable-prompt-tokens-details --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3 --host 127.0.0.1 --port 8000` |
| GPU, driver | NVIDIA H100, 95830 MiB, 580.178.04 |
| CUDA | CUDA Version: 13.0; torch 2.13.0+cu130 13.0 |
| Our repo | 42c00de (econoclm-view) |
| CLM | 18dc111 |
| TBLite | 5c37b41 |
| Harbor | Version: 0.16.1 |
| Python | 3.12.3 |
| Arms, reps, workers | ['clm'], 1, 4; timeout multiplier 4 |
| Config clm | `{"agent": "clm_harness.clm_agent.harness:ClmAgent", "agent_kwargs": {"command_timeout": 180, "context_budget_tokens": 32000, "cost_metric": "flops", "flops_model_key": "27b", "flops_tokenizer": "Qwen/Qwen3.6-27B", "max_steps": 64, "max_tokens": 2048, "send_chat_template_kwargs": true, "temperature": 0.7, "top_p": 0.95}, "model": "openai/qwen36-27b"}` |

Notes: "Calls at max_tokens" counts calls whose generated tokens reached max_tokens (CLM does not record finish reasons; there is no gateway on Qwen). Qwen3.6's chat template strips earlier turns' reasoning, which limits prefix reuse in every arm alike.
