"""A Deep Agents sandbox backend on a SWE-bench instance's official Docker image.

Why it exists: the agent must edit the repository and run its tests in the exact
environment the official evaluation uses. The instance image already has the
repository at /testbed (at the base commit) and its dependencies installed in the
`testbed` conda environment. Deep Agents' `BaseSandbox` implements every file
tool through `execute`, so this class only runs commands and moves bytes.
What it must never do: know anything about EconoContext.

Images are linux/amd64 only; Docker Desktop on Apple Silicon runs them under
emulation (slower, but correct in our v0 runs).
"""

import shlex
import subprocess
import uuid

from deepagents.backends.protocol import ExecuteResponse, FileDownloadResponse, FileUploadResponse
from deepagents.backends.sandbox import BaseSandbox

PLATFORM = "linux/amd64"
# The official images' environment (verified in swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-5809).
ENV_PATH = ("/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/condabin:/opt/miniconda3/bin:"
            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")


def docker(*argv: str, stdin: bytes | None = None, timeout: float = 600) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *argv], input=stdin, capture_output=True, timeout=timeout)


class SweBenchSandbox(BaseSandbox):
    """One running container for one instance. Use as a context manager."""

    def __init__(self, image: str, command_timeout: int = 300):
        self.image = image
        self.command_timeout = command_timeout
        self.name = f"eco-{uuid.uuid4().hex[:12]}"
        self.started = False

    @property
    def id(self) -> str:
        return self.name

    def start(self) -> "SweBenchSandbox":
        result = docker("run", "-d", "--platform", PLATFORM, "--name", self.name, "-w", "/testbed",
                        self.image, "sleep", "infinity")
        if result.returncode:
            raise RuntimeError(f"cannot start {self.image}: {result.stderr.decode()[-400:]}")
        self.started = True
        return self

    def close(self) -> None:
        if self.started:
            docker("rm", "-f", self.name, timeout=120)
            self.started = False

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        limit = int(timeout or self.command_timeout)
        # Enforced inside the container too, so a timed-out command does not keep running.
        wrapped = f"timeout -k 5 {limit} bash -c {shlex.quote(command)}"
        try:
            result = docker("exec", "-w", "/testbed", "-e", f"PATH={ENV_PATH}", self.name,
                            "bash", "-c", wrapped, timeout=limit + 30)
        except subprocess.TimeoutExpired:
            return ExecuteResponse(output=f"[command timed out after {limit}s]", exit_code=124)
        return ExecuteResponse(output=(result.stdout + result.stderr).decode(errors="replace"),
                               exit_code=result.returncode)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        out = []
        for path, data in files:
            q = shlex.quote(path)
            r = docker("exec", "-i", self.name, "bash", "-c", f'mkdir -p "$(dirname {q})" && cat > {q}',
                       stdin=data)
            out.append(FileUploadResponse(path=path, error=None if r.returncode == 0
                                          else (r.stderr.decode()[-200:] or "write failed")))
        return out

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        out = []
        for path in paths:
            r = docker("exec", self.name, "cat", path)
            out.append(FileDownloadResponse(path=path, content=r.stdout) if r.returncode == 0
                       else FileDownloadResponse(path=path, error="file_not_found"))
        return out

    def patch(self) -> str:
        """The agent's change as a git diff against the checked-out base, for evaluation."""
        result = self.execute("git add -A && git diff --cached --binary HEAD")
        return result.output if result.exit_code == 0 else ""
