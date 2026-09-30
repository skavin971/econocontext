"""Run one SWE-bench Verified instance on Omnigent, in one arm, and grade it.

Why it exists: the experiment. It needs the task, a workspace, one Omnigent session,
the patch, and the official grade. Everything EconoContext does happens inside
Omnigent (the policy) and in front of the model (the gateway); this script only sets
the run up and reads the outcome.

  1. register the run (arm, mode) in the Agent DB, so the gateway and policy know it
  2. copy /testbed out of the instance image into data/work/<run>, and start that image
     with the copy mounted at /testbed (the repository's own environment)
  3. create an Omnigent session from harness/specs/openai_agents.yaml (harness/session.py); its testbed_shell tool
     (omnigent_layer.tools.container_shell) runs commands in that container and shows
     the host path in place of /testbed, so the agent sees one path
  4. send the issue; take `git diff`; grade with the official harness

Needs: the Omnigent server (omnigent start) and the gateway
(python -m omnigent_layer.gateway) running, and Docker.

--harness gemini-omnigent runs stock Gemini CLI instead (harness/specs/gemini/agent.yaml): no
container (Gemini's shell runs on the host), no policy; the gateway records its calls
and, in the econo arm, the evidence they carried.

Run: .venv/bin/python benchmarks/swebench/run.py --label dev1 --instance pytest-dev__pytest-5809 --arm econo --mode observe [--jev]
     .venv/bin/python benchmarks/swebench/run.py --harness gemini-omnigent --label g1 --instance pytest-dev__pytest-5809 --arm econo
     .venv/bin/python benchmarks/swebench/run.py --label p1 --set mid5 --arm econo --mode observe
     .venv/bin/python benchmarks/swebench/run.py --label p3 --set mid5 --arm econo --mode autopilot --learned --pointer
"""

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from benchmarks.swebench.evaluate import evaluate  # noqa: E402
from benchmarks.swebench.tasks import SETS, load  # noqa: E402
from harness.session import run_session, write_spec  # noqa: E402
from omnigent_layer import HOME, engine_for, register_run  # noqa: E402

GEMINI_MAX_CALLS = 30  # per task: the API is rate limited
# Gemini gets the issue with the framing the openai-controlled agent has in its spec
# prompt, and nothing about how to work: its own instructions apply.
GEMINI_TASK = ("Fix the GitHub issue below in the repository in your working directory. "
               "Hidden tests will check your fix. Keep the change minimal.\n\n")
ACTIVATE = "source /opt/miniconda3/bin/activate testbed"


def sh(*cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def prepare(image: str, workdir: Path, container: str, run_container: bool = True) -> None:
    """Copy the repository out of the image, then (unless not wanted) run the image with
    the copy mounted."""
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.parent.mkdir(parents=True, exist_ok=True)
    sh("docker", "rm", "-f", container, check=False)
    sh("docker", "create", "--platform", "linux/amd64", "--name", container + "-src", image)
    sh("docker", "cp", f"{container}-src:/testbed", str(workdir))
    sh("docker", "rm", container + "-src")
    # Omnigent's runner probes the workspace with an mtime-test-* directory; keep it out
    # of the patch and of the write barrier.
    with open(workdir / ".git" / "info" / "exclude", "a") as exclude:
        exclude.write("\nmtime-test-*/\n.econocontext/\n")  # Omnigent's probe; our pointer files
    if not run_container:
        return
    # Labels let omnigent_layer.tools.container_shell (testbed_shell) find this container.
    sh("docker", "run", "-d", "--platform", "linux/amd64", "--name", container,
       "--label", f"econocontext.workdir={os.path.realpath(workdir)}",
       "--label", "econocontext.mount=/testbed", "--label", f"econocontext.setup={ACTIVATE}",
       "-v", f"{workdir}:/testbed", "-w", "/testbed", image, "sleep", "infinity")


def main() -> None:
    p = argparse.ArgumentParser(description="Run one SWE-bench instance on Omnigent.")
    p.add_argument("--label", required=True)
    p.add_argument("--harness", choices=["openai-controlled", "gemini-omnigent"],
                   default="openai-controlled")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--instance", help="one SWE-bench Verified instance id")
    which.add_argument("--set", choices=sorted(SETS), help="a fixed set from benchmarks/swebench/tasks.py")
    p.add_argument("--arm", choices=["baseline", "econo"], required=True)
    p.add_argument("--mode", choices=["observe", "autopilot"], default="observe")
    p.add_argument("--jev", action="store_true",
                   help="econo arm: ask planner/jev_planner.py for p_need_again (default: fixed guess)")
    p.add_argument("--max-minutes", type=float, default=20, help="wall-clock cap per task")
    p.add_argument("--learned", action="store_true",
                   help="learned H and p from earlier labelled runs (harness/learn.py label)")
    p.add_argument("--pointer", action="store_true",
                   help="autopilot may use POINTER, COMMIT_PENDING and RESUME (quality risk <= 0.2)")
    p.add_argument("--server", default="http://127.0.0.1:6767")
    p.add_argument("--gateway", default="http://127.0.0.1:8787")
    a = p.parse_args()

    gemini = a.harness == "gemini-omnigent"
    if gemini and (a.jev or a.learned or a.pointer or a.mode != "observe"):
        p.error("gemini-omnigent is measured only: --mode observe, no --jev/--learned/--pointer")
    instances = SETS[a.set] if a.set else [a.instance]
    overrides = {}
    if a.learned:
        overrides["learned"] = True
    if a.pointer:
        overrides.update(allowlist={"POINTER": True, "COMMIT_PENDING": True, "RESUME": True},
                         constraints={"max_quality_risk": 0.2})
    for instance in instances:
        run_one(a, instance, overrides)


def run_one(a, instance: str, overrides: dict) -> None:
    inst = load([instance])[0]
    arm = "econo+jev" if a.jev and a.arm == "econo" else a.arm
    run_id = f"{a.label}:{arm}:{instance}"
    safe = re.sub(r"[^\w.-]", "_", run_id)
    workdir = HOME / "data" / "work" / safe
    container = "econo-" + safe.lower()[:60]
    gemini = a.harness == "gemini-omnigent"
    spec = write_spec(a.harness, run_id, workdir, a.gateway, a.arm == "econo")
    if gemini:
        overrides = {**overrides, "limits": {"max_model_calls": GEMINI_MAX_CALLS}}
    register_run(run_id, a.arm, a.mode, instance, jev=a.jev and a.arm == "econo",
                 current=not gemini, overrides=overrides or None, workdir=str(workdir),
                 host="omnigent:gemini" if gemini else "omnigent")
    print(f"== {run_id}", flush=True)

    status = "done"
    try:
        prepare(inst.image, workdir, container, run_container=not gemini)
        task = GEMINI_TASK + inst.problem_statement if gemini else inst.problem_statement
        summary, _ = asyncio.run(run_session(a.server, spec, workdir, task, a.max_minutes * 60,
                                             approve=gemini))
        print("agent:", summary[:300])
    except TimeoutError:
        status = "timeout"
        print(f"stopped after {a.max_minutes} minutes")
    except Exception as exc:  # the agent's failure is a result, not a crash of the bench
        status = f"error: {str(exc)[:300]}"
        print(status)
    finally:
        sh("docker", "rm", "-f", container, check=False)

    sh("git", "-C", str(workdir), "add", "-A")
    patch = sh("git", "-C", str(workdir), "diff", "--cached", "HEAD").stdout
    out = HOME / "data" / "runs" / a.label
    out.mkdir(parents=True, exist_ok=True)
    predictions = out / f"{safe}.jsonl"
    predictions.write_text(json.dumps({"instance_id": instance, "model_patch": patch,
                                       "model_name_or_path": (f"omnigent-gemini-{a.arm}" if gemini
                                                              else f"omnigent-{a.arm}")}) + "\n")
    engine, _ = engine_for(run_id)
    engine.end_run(status)
    grade = evaluate(predictions, [instance], run_id=f"{safe}-eval")
    engine.db.set_resolved(run_id, grade["per_instance"][instance])
    print(json.dumps({"run_id": run_id, "status": status, "patch_bytes": len(patch),
                      "resolved": grade["per_instance"][instance]}), flush=True)


if __name__ == "__main__":
    main()
