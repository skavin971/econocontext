"""PFLOP accuracy, step 1: set everything up (pflop_accuracy/README.md). Safe to run again: done steps are skipped.

Use (from the repository root, with Python 3.12):  python3.12 pflop_accuracy/setup.py
     only check, install nothing:                 python3.12 pflop_accuracy/setup.py --check

It creates the three Python environments, clones BrowseComp-Plus, decrypts its questions and answers with
BrowseComp-Plus's own script (a 2.8 GB download), downloads the prebuilt BM25 index (2.2 GB) and writes the Harbor task
folders for the pilot questions. Everything from BrowseComp-Plus stays in ~/econo/bcp/, outside the repository; its
text must never be committed or posted (the benchmark's canary). Nothing here prints question or answer text.
"""

import argparse
import json
import shutil
from pathlib import Path
import subprocess
import sys

from common import (BCP, BCP_COMMIT, BCP_REPO, CLM_HARBOR, DATA, HERE, INDEX, PY, ROOT, SEARCH_PY, TASKS, env_has,
                    run)

OK, TODO = "ok  ", "MISSING"


def python_ok() -> bool:
    return sys.version_info[:2] in ((3, 12), (3, 13))


def step(name: str, done: bool, fix, check_only: bool) -> bool:
    if done:
        print(f"[{OK}] {name}")
        return True
    if check_only or fix is None:
        print(f"[{TODO}] {name}")
        return False
    print(f"[....] {name}: setting up")
    fix()
    return True


def venv(path, packages: list, constraints=None):
    def make():
        run([sys.executable, "-m", "venv", path])
        pip = [path / "bin" / "python", "-m", "pip"]
        run(pip + ["install", "-q", "--upgrade", "pip"])
        run(pip + ["install", "-q"] + packages + (["-c", constraints] if constraints else []))
    return make


def decrypt():
    """BrowseComp-Plus's own decryption, then the two small files our code reads (query_id+query, query_id+answer)."""
    full = DATA / "browsecomp_plus_decrypted.jsonl"
    if not full.exists():
        run([SEARCH_PY, "scripts_build_index/decrypt_dataset.py", "--output", "data/browsecomp_plus_decrypted.jsonl"],
            cwd=BCP_REPO)
    n = 0
    with full.open() as src, (DATA / "queries_decrypted.jsonl").open("w") as q, (DATA / "answers_decrypted.jsonl").open("w") as a:
        for line in src:
            if line.strip():
                x = json.loads(line)
                q.write(json.dumps({"query_id": x["query_id"], "query": x["query"]}, ensure_ascii=False) + "\n")
                a.write(json.dumps({"query_id": x["query_id"], "answer": x["answer"]}, ensure_ascii=False) + "\n")
                n += 1
    print(f"       {n} questions written (text stays in {DATA})")


def index():
    run([SEARCH_PY, "-c", "from huggingface_hub import snapshot_download; snapshot_download("
         "'Tevatron/browsecomp-plus-indexes', repo_type='dataset', allow_patterns=['bm25/*'], "
         f"local_dir='{BCP_REPO / 'indexes'}')"])


def java21() -> str | None:
    """A Java 21 binary: JAVA_HOME, then PATH, then the usual Linux and Homebrew places."""
    import glob
    import os
    candidates = [os.path.join(os.environ.get("JAVA_HOME", ""), "bin", "java"), shutil.which("java") or ""]
    candidates += glob.glob("/usr/lib/jvm/java-21*/bin/java") + glob.glob("/opt/homebrew/opt/openjdk@21/bin/java")
    for java in candidates:
        if java and os.path.exists(java):
            out = subprocess.run([java, "-version"], capture_output=True, text=True)
            if '"21' in (out.stderr + out.stdout):
                return java
    return None


def java_ok() -> bool:
    java = java21()
    if java:
        home = str(Path(java).resolve().parent.parent)
        print(f"       Java 21: {java} (start the search service with JAVA_HOME={home})")
    return java is not None


def docker_ok() -> bool:
    return shutil.which("docker") is not None and subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def main() -> None:
    ap = argparse.ArgumentParser(description="PFLOP accuracy: set everything up")
    ap.add_argument("--check", action="store_true", help="only report what is ready; install nothing")
    a = ap.parse_args()
    if not python_ok():
        raise SystemExit(f"run this with Python 3.12 (CLM needs 3.12 or 3.13); this is {sys.version.split()[0]}")
    c = a.check
    print("PFLOP accuracy: setup" + (" (check only)" if c else ""))
    BCP.mkdir(parents=True, exist_ok=True)
    ok = [
        step("Docker running", docker_ok(), None, c),
        step("Java 21 (for the BM25 search service)", java_ok(), None, c),
        step("CLM's code (git submodule baselines/clm/upstream)", (ROOT / "baselines/clm/upstream/pyproject.toml").exists(),
             lambda: run(["git", "submodule", "update", "--init", "baselines/clm/upstream"]), c),
        step(".venv (our measurement code and runner)", PY.exists(),
             venv(ROOT / ".venv", ["-e", f"{ROOT}[dev,measure]"]), c),
        step(".venv-clm (CLM's agent, as released)", CLM_HARBOR.exists(),
             venv(ROOT / ".venv-clm", ["-e", f"{ROOT / 'baselines/clm/upstream'}[hf]"], HERE / "requirements-clm.txt"), c),
        step(f"BrowseComp-Plus clone at {BCP_COMMIT} ({BCP_REPO})", (BCP_REPO / ".git").exists(),
             lambda: (run(["git", "clone", "-q", "https://github.com/texttron/BrowseComp-Plus.git", BCP_REPO]),
                      run(["git", "checkout", "-q", BCP_COMMIT], cwd=BCP_REPO)), c),
        step(f"search environment ({SEARCH_PY.parent.parent})", SEARCH_PY.exists(),
             venv(BCP / ".venv", ["-r", HERE / "requirements-search.txt"]), c),
        step("BrowseComp-Plus questions and answers, decrypted (2.8 GB download)",
             (DATA / "queries_decrypted.jsonl").exists() and (DATA / "answers_decrypted.jsonl").exists(), decrypt, c),
        step("BM25 index (2.2 GB download)", INDEX.exists() and any(INDEX.iterdir()), index, c),
        step(f"Harbor tasks for the pilot questions ({TASKS})", (TASKS / "591" / "instruction.md").exists()
             and (TASKS / "591" / "environment" / "docker-compose.yaml").exists(),
             lambda: run([PY, "benchmarks/bcp/tasks.py", "--split", "pilot"]), c),
        step("Purdue key in .env (GENAI_API_KEY; Path A and the optional judge only)", env_has("GENAI_API_KEY"), None, c),
    ]
    print()
    if all(ok[:-1]):
        print("Ready. Next: start the services (README, Path A or Path B), then: python3 pflop_accuracy/check_ready.py")
    else:
        print("Not ready: fix the MISSING lines (README, 'Requirements'), then run this again.")
        if not ok[0]:
            print("  Docker: install Docker Engine and make sure `docker info` works for your user.")
        if not ok[1]:
            print("  Java 21: sudo apt install openjdk-21-jdk  (then: export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64)")
        if not ok[-1]:
            print("  .env: add a line GENAI_API_KEY=<your own Purdue GenAI Studio key> (never commit .env)")


if __name__ == "__main__":
    main()
