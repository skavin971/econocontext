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
