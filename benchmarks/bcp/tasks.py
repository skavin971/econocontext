"""BrowseComp-Plus as Harbor tasks (step 13a). CLM did not release its BrowseComp-Plus task folders or search
skill, so this builds them from the CLM paper's Appendix E and BrowseComp-Plus's tools: "CLM agent as released;
task and search packaging built from Appendix E".

One task per question, written OUTSIDE the repo (BrowseComp-Plus asks that its text never appear online):
  ~/econo/bcp/tasks/<query_id>/  task.toml, instruction.md (prompts.TASK_BRIEF with the question),
                                 environment/Dockerfile (python:3.12-slim, /app),
                                 environment/docker-compose.yaml (host.docker.internal -> the host, for Linux
                                 Docker; Docker Desktop already has it), tests/test.sh
The verifier only keeps the answer file (/app/answer.md -> /logs/verifier/answer.md) and writes reward 0; the
judge grades the answers afterwards (benchmarks/bcp/judge.py). Search goes to the one local service
(search_server.py); CLM reaches it through the skill in skill/bcp_search, our harness through its bcp tools.

Use: .venv/bin/python benchmarks/bcp/tasks.py --split pilot
"""

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
BCP = Path.home() / "econo" / "bcp"
QUERIES = BCP / "BrowseComp-Plus" / "data" / "queries_decrypted.jsonl"
TASKS = BCP / "tasks"
SKILL = HERE / "skill" / "bcp_search"
SPLIT = HERE / "split.json"

TASK_TOML = """version = "1.0"

[metadata]
source = "BrowseComp-Plus (Tevatron/browsecomp-plus), query_id {qid}"
category = "deep-research"

[verifier]
timeout_sec = 120.0

[agent]
timeout_sec = 7200.0

[environment]
build_timeout_sec = 600.0
"""
DOCKERFILE = "FROM python:3.12-slim\n\nWORKDIR /app\n"
# Harbor merges a task's environment/docker-compose.yaml into its own (service "main"). The search skill calls
# http://host.docker.internal:8790, which Linux Docker Engine only resolves with this mapping.
COMPOSE = """services:
  main:
    extra_hosts:
      - "host.docker.internal:host-gateway"
"""
TEST_SH = """#!/bin/bash
# Keep the answer file for the judge (benchmarks/bcp/judge.py grades it later); reward 0 here.
mkdir -p /logs/verifier
cp /app/answer.md /logs/verifier/answer.md 2>/dev/null || echo "(no answer file)" > /logs/verifier/answer.md
echo 0 > /logs/verifier/reward.txt
"""


def questions() -> dict[str, str]:
    return {str(x["query_id"]): x["query"] for x in map(json.loads, (l for l in QUERIES.read_text().split("\n") if l))}


def build(ids: list[str], dest: Path = TASKS) -> list[Path]:
    import sys
    sys.path.insert(0, str(HERE))
    from prompts import TASK_BRIEF
    text = questions()
    out = []
    for qid in ids:
        d = dest / qid
        (d / "environment").mkdir(parents=True, exist_ok=True)
        (d / "tests").mkdir(parents=True, exist_ok=True)
        (d / "task.toml").write_text(TASK_TOML.format(qid=qid))
        (d / "instruction.md").write_text(TASK_BRIEF.format(Question=text[qid]) + "\n")
        (d / "environment" / "Dockerfile").write_text(DOCKERFILE)
        (d / "environment" / "docker-compose.yaml").write_text(COMPOSE)
        (d / "tests" / "test.sh").write_text(TEST_SH)
        (d / "tests" / "test.sh").chmod(0o755)
        out.append(d)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=("pilot", "dev", "test"), default="pilot")
    a = ap.parse_args()
    ids = json.loads(SPLIT.read_text())[a.split]
    print(f"{len(build(ids))} tasks written to {TASKS} ({a.split})")


if __name__ == "__main__":
    main()
