"""benchmarks/bcp/tasks.py: every task gets the compose file that lets the search skill reach the host on Linux."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_a_task_maps_host_docker_internal_to_the_host(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("bcp_tasks", ROOT / "benchmarks" / "bcp" / "tasks.py")
    tasks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tasks)
    monkeypatch.setattr(tasks, "questions", lambda: {"7": "placeholder question"})
    (d,) = tasks.build(["7"], dest=tmp_path)
    compose = (d / "environment" / "docker-compose.yaml").read_text()
    assert "main:" in compose and "host.docker.internal:host-gateway" in compose
    assert (d / "environment" / "Dockerfile").exists() and (d / "tests" / "test.sh").exists()


def test_instructed_tasks_append_the_papers_threshold_instruction_and_nothing_else(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("bcp_tasks", ROOT / "benchmarks" / "bcp" / "tasks.py")
    tasks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tasks)
    monkeypatch.setattr(tasks, "questions", lambda: {"7": "placeholder question"})
    (plain,) = tasks.build(["7"], dest=tmp_path / "plain")
    (inst,) = tasks.build(["7"], dest=tmp_path / "inst", instruction=tasks.INSTRUCTION)
    a, b = (plain / "instruction.md").read_text(), (inst / "instruction.md").read_text()
    assert b == a.rstrip("\n") + "\n\n" + tasks.INSTRUCTION + "\n"
    assert "exceeds 16,000 tokens" in tasks.INSTRUCTION and "/tmp/.live_ctx/LIVE_CTX_MAIN.txt" in tasks.INSTRUCTION
    # The task folder carries no answer: only the task text, the environment and a verifier that copies the answer.
    files = sorted(str(p.relative_to(inst)) for p in inst.rglob("*") if p.is_file())
    assert files == ["environment/Dockerfile", "environment/docker-compose.yaml", "instruction.md", "task.toml",
                     "tests/test.sh"]
    assert "answer" not in (inst / "task.toml").read_text().lower()
