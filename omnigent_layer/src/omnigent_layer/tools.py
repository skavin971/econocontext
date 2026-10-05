"""Tools the platform layer gives agents. One so far: a shell inside the workspace's container.

Why it exists: an agent's own shell (sys_os_shell) runs on the host, which lacks the
repository's environment. `container_shell` runs a command in the Docker container
that has the agent's work directory mounted, so tests run where they should. Omnigent's
runner calls it in the session's working directory; the container is found by its
labels, which whoever starts the container sets:

    econocontext.workdir  the host work directory (must equal the runner's cwd)
    econocontext.mount    where it is mounted inside the container, e.g. /testbed
    econocontext.setup    optional shell prefix, e.g. activating an environment

Use it in an agent spec:

    tools:
      testbed_shell:
        type: function
        callable: omnigent_layer.tools.container_shell
        parameters: {type: object, properties: {command: {type: string}}, required: [command]}

Output shows the host path wherever the container path appears, so the agent sees one
path for the same files.
What it must never do: run anything on the host.
"""

import json
import os
import re
import subprocess
import time
import uuid

from econocontext.observation import observe
from .research import recorder_for_workspace

TIMEOUT_S = 300
MAX_OUTPUT_CHARS = 30_000  # keeps one command's output from flooding the window


def _container_for(workdir: str) -> tuple[str, dict[str, str]] | None:
    ids = subprocess.run(["docker", "ps", "-q", "--filter", f"label=econocontext.workdir={workdir}"],
                         capture_output=True, text=True).stdout.split()
    if not ids:
        return None
    labels = subprocess.run(["docker", "inspect", "--format", "{{json .Config.Labels}}", ids[0]],
                            capture_output=True, text=True).stdout
    return ids[0], json.loads(labels or "{}")


def container_shell(command: str) -> str:
    """Run a bash command in the repository's own environment (cwd: the repository root)."""
    workdir = os.path.realpath(os.getcwd())
    sink = recorder_for_workspace(workdir)
    started = time.monotonic()
    execution_id = uuid.uuid4().hex
    links = dict(source="container_shell", call_id=execution_id)
    observe(sink, "shell.started", {"command": command, "workspace": workdir}, **links)
    try:
        found = _container_for(workdir)
    except Exception as exc:
        observe(sink, "shell.error", {"type": type(exc).__name__}, **links)
        raise
    if found is None:
        observe(sink, "shell.error", {"type": "container_unavailable"}, **links)
        return f"error: no running container is bound to this workspace ({workdir})"
    container, labels = found
    mount = labels.get("econocontext.mount", "/workspace")
    setup = labels.get("econocontext.setup")
    script = f"{setup} && cd {mount} && {command}" if setup else f"cd {mount} && {command}"
    try:
        done = subprocess.run(["docker", "exec", container, "bash", "-lc", script],
                              capture_output=True, text=True, timeout=TIMEOUT_S)
        code, out, err = done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired as exc:
        observe(sink, "shell.output", {"command": command, "exit_code": None,
                "stdout": exc.stdout or b"", "stderr": exc.stderr or b"", "timed_out": True,
                "duration_ms": (time.monotonic() - started) * 1000}, **links)
        code, out, err = -1, "", f"timed out after {TIMEOUT_S} s"
    except Exception as exc:
        observe(sink, "shell.error", {"type": type(exc).__name__}, **links)
        raise
    else:
        observe(sink, "shell.output", {"command": command, "exit_code": code,
                "stdout": out, "stderr": err, "timed_out": False,
                "duration_ms": (time.monotonic() - started) * 1000}, **links)
    text = f"exit code: {code}\nstdout:\n{out}\nstderr:\n{err}"
    # Only the mount path itself (e.g. /testbed/src/x.py), not names that contain it
    # (e.g. /opt/miniconda3/envs/testbed).
    text = re.sub(rf"(?<![\w.-]){re.escape(mount)}(?![\w.-])", lambda _: workdir, text)
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "\n[output truncated]"
    return text
