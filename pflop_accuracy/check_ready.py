"""PFLOP accuracy: is everything running? One line per check, with the fix (pflop_accuracy/README.md).

Use:  python3 pflop_accuracy/check_ready.py --backend purdue      (Path A)
      python3 pflop_accuracy/check_ready.py --backend vllm        (Path B)
"""

import argparse
import shutil
import subprocess

from common import CLM_HARBOR, DATA, GATEWAY, GATEWAY_LOGS, MODEL, SEARCH, TASKS, env_has, get_json

GATEWAY_CMD = (".venv/bin/python -m gateway.server --provider {p} {up}--max-tokens-for bcp=4096 "
               "--log-dir-for bcp=~/econo/bcp/gateway --max-calls-per-run 300{rpm}")


def line(ok: bool, what: str, fix: str = "") -> bool:
    print(f"[{'ok  ' if ok else 'FAIL'}] {what}" + ("" if ok or not fix else f"\n       fix: {fix}"))
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description="PFLOP accuracy: check that everything is running")
    ap.add_argument("--backend", required=True, choices=("purdue", "vllm"))
    ap.add_argument("--vllm", default="http://127.0.0.1:8000/v1", help="the vLLM server (Path B)")
    a = ap.parse_args()
    gateway_cmd = GATEWAY_CMD.format(p=a.backend, up=f"--upstream {a.vllm} " if a.backend == "vllm" else "",
                                     rpm=" --rpm 600" if a.backend == "vllm" else "")
    print(f"PFLOP accuracy: ready check for Path {'A (Purdue)' if a.backend == 'purdue' else 'B (our vLLM)'}")
    ok = [line(shutil.which("docker") is not None and subprocess.run(["docker", "info"], capture_output=True).returncode == 0,
               "Docker running", "install Docker Engine; make sure `docker info` works for your user"),
          line(CLM_HARBOR.exists(), "CLM's agent installed (.venv-clm)", "python3.12 pflop_accuracy/setup.py"),
          line((DATA / "queries_decrypted.jsonl").exists() and (TASKS / "591" / "instruction.md").exists(),
               "BrowseComp-Plus data and tasks in ~/econo/bcp", "python3.12 pflop_accuracy/setup.py")]
    s = get_json(f"{SEARCH}/health")
    ok.append(line(bool(s) and s.get("k") == 10 and s.get("snippet_tokens") == 512,
                   "search service on :8790 (BM25, top 10, 512-token snippets)",
                   "in its own terminal: JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 ~/econo/bcp/.venv/bin/python "
                   "benchmarks/bcp/search_server.py --index ~/econo/bcp/BrowseComp-Plus/indexes/bm25 --host 0.0.0.0"))
    if a.backend == "vllm":
        models = get_json(f"{a.vllm.rstrip('/')}/models", timeout=10)
        names = [m.get("id") for m in (models or {}).get("data", [])]
        ok.append(line(MODEL in names, f"vLLM at {a.vllm} serves {MODEL}",
                       "start vLLM as in README Path B, step B1 (it must use --served-model-name qwen3.8:27b)"))
    else:
        ok.append(line(env_has("GENAI_API_KEY"), "Purdue key in .env (GENAI_API_KEY)",
                       "add a line GENAI_API_KEY=<your own key> to .env (never commit it)"))
    g = get_json(f"{GATEWAY}/health")
    ok.append(line(bool(g) and g.get("provider") == a.backend, f"gateway on :8787 with provider {a.backend}",
                   f"in its own terminal: {gateway_cmd}" + ("" if not g else f"   (it now runs provider {g.get('provider')})")))
    if g:
        ok.append(line((g.get("max_tokens_for") or {}).get("bcp") == 4096 and bool((g.get("log_dir_for") or {}).get("bcp")),
                       f"gateway pins 4,096 output tokens for bcp runs and logs them to {GATEWAY_LOGS}",
                       f"restart the gateway: {gateway_cmd}"))
        if a.backend == "vllm":
            ok.append(line((g.get("upstream") or "").rstrip("/") == a.vllm.rstrip("/"), f"gateway forwards to {a.vllm}",
                           f"restart the gateway: {gateway_cmd}"))
    print()
    print("Ready." if all(ok) else "Not ready: fix the FAIL lines, then run this again.")
    if all(ok):
        print("Next: " + ("python3 pflop_accuracy/probe_vllm_cache.py, then " if a.backend == "vllm" else "")
              + f".venv/bin/python pflop_accuracy/run_clm.py --backend {a.backend}")


if __name__ == "__main__":
    main()
