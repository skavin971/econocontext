# Running the arms on Qwen3.6-27B (one H100)

**The three arms** (one run = one TBLite task, start to finish):
- **CLM**: CLM exactly as released.
- **EconoCLM-Tools**: CLM plus a lossless output database (`econo get` / `econo search` / `econo note`) and an `[econo]` line with compute facts.
- **EconoCLM-View**: the model edits `VIEW.md`, the list of what goes into its next prompt. *Locked until its fixed Gemini check is approved.*

**Out of scope:** budgets other than 32K, v2, skill evolution, Suffix Cache Reuse, RL, the gateway, BCP, EdgeBench.

**For now, run only:** `run_qwen.sh --arms clm --reps 3` (10 tasks × 3 runs = 30 trials).

## 1. Node prep (once)

- **NVIDIA:** a driver new enough for CUDA 12.8+ (`nvidia-smi` must work).
- **Docker:** `docker ps` must work without sudo (add yourself to the `docker` group).
- **Python 3.12:** `python3.12 --version`.
- **Hugging Face:** `huggingface-cli login`. The model isn't gated, but logging in avoids download rate limits.
- **Disk:** about **150 GB free**. That covers the model (about 54 GB in bf16), the vLLM venv, the Docker task images and the runs.
- **tmux.**

## 2. Clones and installs

```sh
mkdir -p ~/econo && cd ~/econo
git clone -b econoclm-view https://github.com/skavin971/econocontext
git clone https://github.com/facebookresearch/context-language-models && git -C context-language-models checkout 18dc111
git clone https://github.com/open-thoughts/OpenThoughts-TBLite && git -C OpenThoughts-TBLite checkout 5c37b41
cd econocontext
python3.12 -m venv .venv-econoclm && . .venv-econoclm/bin/activate
pip install -U pip
pip install -e "../context-language-models[hf]"   # CLM, harbor 0.16.1, transformers (Qwen tokenizer)
pip install -e "econoclm/[test]"
harbor --version                                   # 0.16.1
```

## 3. Start the server (tmux window 1)

```sh
tmux new -s qwen
cd ~/econo/econocontext && bash backends/qwen/serve.sh
```

- The first start installs **vLLM 0.30.0** into its own venv (`~/econo/.venv-vllm`) and downloads **Qwen/Qwen3.6-27B @ `6a9e13b`**.
- Wait for vLLM's "Application startup complete".
- Leave the server running. **Don't change `serve.sh`.** Every arm must use the same vLLM version, revision and flags; a restart is fine.

## 4. Run (tmux window 2: `Ctrl-b c`)

```sh
cd ~/econo/econocontext && bash econoclm/bench/tblite/run_qwen.sh --arms clm --reps 3
```

What the run does:
1. **Checks the server.** It must answer, serve `qwen36-27b`, and report cached tokens. If not, the run stops with the fix.
2. **Runs one check trial on the first task.** That trial must produce a reward and FLOPs, or the run stops.
3. **Prints a projection** of the total wall time from the check trial.
4. **Runs the rest,** 4 trials at a time.
5. **Writes the report** at the end: `runs-qwen/<date>/REPORT_QWEN.md`.

- **Resumable:** if it stops, run the same command again with `--date <the run's date>`. Finished trials are skipped; unfinished ones are rerun. `--skip-checks` skips steps 1–2.
- **Options:** `--arms` (comma list), `--reps N`, `--workers N`, `--dry-run` (prints the commands).

## 5. Send back the summary

```sh
D=<the run's date>
git checkout -b econoclm-qwen-results
git add -f runs-qwen/$D/REPORT_QWEN.md runs-qwen/$D/results_qwen.csv runs-qwen/$D/settings.json
git commit -m "Qwen: CLM baseline, 10 tasks x 3 runs ($D)" && git push -u origin econoclm-qwen-results
```

The per-trial folders (`runs-qwen/<date>/trials/`) aren't committed. Zip them only if asked.

## Later (only when told)

1. `git pull` on `econoclm-view`.
2. Restart `serve.sh` unchanged: same vLLM version, model revision and flags.
3. Run the other two arms into the **same date folder** as the baseline, so one report compares all three:

```sh
bash econoclm/bench/tblite/run_qwen.sh --arms econo_tools,econo_view --reps 3 --date <baseline date>
```

Until then, EconoCLM-View refuses to run (it needs `--allow-unvalidated`). Don't use that flag unless told.
`REPORT_QWEN.md` records every version and setting, so the later arms can be checked against the baseline's setup.
