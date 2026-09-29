"""container_shell: finds the workspace's container, and shows one path to the agent."""

import json
import subprocess

from omnigent_layer import tools


def fake_docker(stdout_of_exec: str, running: bool = True):
    def run(cmd, **kwargs):
        if cmd[:2] == ["docker", "ps"]:
            out = "abc123\n" if running else ""
        elif cmd[:2] == ["docker", "inspect"]:
            out = json.dumps({"econocontext.mount": "/testbed", "econocontext.setup": "activate"})
        else:
            fake_docker.last_exec = cmd
            out = stdout_of_exec
        return subprocess.CompletedProcess(cmd, 0, out, "")
    return run


def test_runs_in_the_container_and_rewrites_only_the_mount_path(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tools.subprocess, "run",
                        fake_docker("/testbed/src/a.py\n/opt/conda/envs/testbed/bin/python\n"))
    out = tools.container_shell("pytest -q")
    work = str(tmp_path.resolve())
    assert f"{work}/src/a.py" in out and "/opt/conda/envs/testbed/bin/python" in out
    assert fake_docker.last_exec[-1] == "activate && cd /testbed && pytest -q"


def test_without_a_bound_container_it_refuses(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tools.subprocess, "run", fake_docker("", running=False))
    assert tools.container_shell("ls").startswith("error: no running container")
