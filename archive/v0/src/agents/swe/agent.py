"""An agent that fixes a real issue in a real repository, with its tools in a container.

The repository is copied out of the task's image into the run workspace, so the
harness reads, searches and tracks staleness on ordinary files exactly as it does
for every other agent. A container from the same image runs with that workspace
mounted at /testbed, which is where `bash` executes. Verification never trusts
that container: it applies the agent's diff and the task's own test patch in a
fresh one and runs the task's FAIL_TO_PASS and PASS_TO_PASS tests.
"""

import asyncio
import json
import re
import shlex
import shutil
import uuid
from hashlib import sha256
from pathlib import Path

from econocontext.agent import controls

from ..base import MAX_BYTES, READ_BUDGET, BaseAgent
from . import prompts
from .tools import BASH, PATCH, READ, SEARCH

PLATFORM = "linux/amd64"
ACTIVATE = "source /opt/conda/etc/profile.d/conda.sh && conda activate testbed"
SEARCH_LINES = 100

# Runs inside the fresh verification container. pytest's -rA summary gives one
# "STATUS test-id" line per test, which is what SWE-rebench's own parser reads.
RUN_TESTS = """
import json, subprocess, sys
ids = json.load(open("/eval/tests.json"))
args = ["--no-header", "-rA", "--tb=line", "-p", "no:cacheprovider",
        "-W", "ignore::DeprecationWarning"]
sys.exit(subprocess.call([sys.executable, "-m", "pytest", *args, *ids]))
"""


def clip(text, budget=READ_BUDGET):
    """Keep the start and the end: a test run's verdict is at the bottom."""
    if len(text) <= budget:
        return text, False
    head = budget // 4
    tail = budget - head
    omitted = len(text) - budget
    return f"{text[:head]}\n... [{omitted} characters omitted] ...\n{text[-tail:]}", True


def statuses(log):
    result = {}
    for line in log.splitlines():
        match = re.match(r"^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\s+(\S+)", line)
        if match:
            result[match.group(2)] = match.group(1)
    return result


class SweAgent(BaseAgent):
    name = "swe"
    default_fixture = None
    fixtures = Path(__file__).parent

    def __init__(self, task, workspace, memory, run_id, timeout):
        if not task.fixture:
            raise ValueError("The SWE agent needs a fixture: a SWE-rebench instance id")
        if not (self.fixtures / "fixtures" / task.fixture / "task.json").is_file():
            raise ValueError(f"No SWE fixture named {task.fixture!r}")
        super().__init__(task, workspace, memory, run_id, timeout)
        self.spec = json.loads((self.fixture_root(self.fixture) / "task.json").read_text())
        self.container = f"econocontext-{run_id}"
        self.started = False

    # -- container ----------------------------------------------------------

    async def docker(self, *argv, timeout=None):
        saved = self.timeout
        if timeout:
            self.timeout = timeout
        try:
            return await self.command(["docker", *argv], cwd=self.workspace.parent)
        finally:
            self.timeout = saved

    async def prepare(self):
        self.workspace.parent.mkdir(parents=True, exist_ok=True)
        if self.workspace.exists():
            shutil.rmtree(self.workspace)
        image = self.spec["image"]
        staging = f"{self.container}-stage"
        created = await self.docker("create", "--platform", PLATFORM, "--name", staging, image)
        if created["code"]:
            raise RuntimeError(f"Cannot create container from {image}: {created['output']}")
        try:
            copied = await self.docker(
                "cp", f"{staging}:/testbed", str(self.workspace), timeout=600
            )
            if copied["code"]:
                raise RuntimeError(f"Cannot copy /testbed: {copied['output']}")
        finally:
            await self.docker("rm", "-f", staging)
        # Files the image's own install left untracked (egg-info and the like) are
        # not the agent's work, and would make the exported diff fail to apply.
        untracked = await self.git("ls-files", "--others", "--exclude-standard")
        with open(self.workspace / ".git" / "info" / "exclude", "a") as exclude:
            for line in untracked["output"].splitlines():
                exclude.write("/" + line + "\n")
        run = await self.docker(
            "run", "-d", "--platform", PLATFORM, "--name", self.container,
            "-v", f"{self.workspace.resolve()}:/testbed", "-w", "/testbed",
            image, "sleep", "infinity",
        )  # fmt: skip
        if run["code"]:
            raise RuntimeError(f"Cannot start task container: {run['output']}")
        self.started = True
        return self.memory.artifacts.json(
            dict(image=image, base_commit=self.spec["base_commit"], instance=self.fixture)
        )

    async def close(self):
        if self.started:
            self.started = False
            # Shielded: a cancelled run must still remove its container. The
            # manager also waits for a finished run's cleanup before closing.
            await asyncio.shield(self.docker("rm", "-f", self.container))

    async def git(self, *argv):
        return await self.command(["git", "-C", str(self.workspace), *argv])

    # -- what the harness tracks ----------------------------------------------

    def files(self):
        # A real repository has thousands of files. Staleness only matters for what
        # was read, so tracking is scoped by refresh() rather than capped here.
        return []

    async def refresh(self):
        """Re-observe every source already read whose content has since changed."""
        current = await self.memory.versions(self.run_id)
        for source in current:
            if source.startswith("tool:"):
                continue
            try:
                path = self.path(source)
            except PermissionError:
                continue
            if not path.is_file():
                await self.memory.write(
                    "DELETE FROM bindings WHERE run_id=? AND source=?", (self.run_id, source)
                )
                continue
            try:
                text = path.read_text()
            except UnicodeDecodeError:
                continue
            if current[source] != sha256(text.encode()).hexdigest():
                await self.memory.observe(self.run_id, source, text)

    # -- what the model sees and can do -----------------------------------------

    def prompt(self, worker, method):
        template = prompts.CHILD if worker.role == "child" else prompts.ROOT
        return template.format(goal=self.goal)

    def tools(self, worker, method):
        extra = [PATCH, BASH] if worker.role == "root" else []
        return [READ, SEARCH] + extra + controls(worker, method, self.delegation_tool)

    async def execute(self, name, arguments, worker):
        if name == "read":
            return await self.read(arguments)
        if name == "search":
            return await self.search(arguments)
        if worker.role != "root":
            raise PermissionError("Children cannot edit files or run commands")
        if name == "apply_patch":
            path = self.path(arguments["path"])
            if not arguments["old"]:
                if path.exists():
                    raise ValueError("File exists; give the exact span to replace")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(arguments["new"])
            else:
                text = path.read_text()
                if text.count(arguments["old"]) != 1:
                    raise ValueError("Patch must match exactly one nonempty source span")
                path.write_text(text.replace(arguments["old"], arguments["new"], 1))
            await self.refresh()
            return dict(applied=True)
        if name == "bash":
            return await self.bash(arguments["command"])
        raise ValueError("Unknown domain tool")

    async def read(self, arguments):
        path = self.path(arguments["path"])
        if not path.is_file():
            raise FileNotFoundError(arguments["path"])
        if path.stat().st_size > 20 * MAX_BYTES:
            raise ValueError("File too large to read")
        text = path.read_text(errors="replace")
        evidence = await self.memory.observe(self.run_id, arguments["path"], text)
        lines = text.splitlines(True)
        start = max(1, int(arguments.get("start_line") or 1))
        end = min(len(lines), int(arguments.get("end_line") or len(lines)))
        shown, truncated = clip("".join(lines[start - 1 : end]))
        return dict(
            text=shown,
            start_line=start,
            end_line=end,
            total_lines=len(lines),
            evidence=[evidence.id],
            truncated=truncated,
        )

    async def search(self, arguments):
        argv = ["grep", "-n", "-I", "-F", "--max-count=20", "-e", arguments["query"]]
        if arguments.get("path"):
            self.path(arguments["path"])
            argv += ["--", arguments["path"]]
        found = await self.git(*argv)
        lines = found["output"].splitlines()
        return dict(
            output="\n".join(lines[:SEARCH_LINES]),
            total=len(lines),
            truncated=len(lines) > SEARCH_LINES or found["truncated"],
        )

    async def bash(self, command):
        # The limit is enforced inside the container too, so a timed-out command
        # does not keep running after the client is killed.
        limit = max(1, int(self.timeout) - 2)
        script = f"{ACTIVATE} && cd /testbed && timeout -k 2 {limit} bash -c {shlex.quote(command)}"
        saved, self.timeout = self.timeout, self.timeout + 5
        try:
            process = await asyncio.create_subprocess_exec(
                "docker", "exec", self.container, "bash", "-c", script,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )  # fmt: skip
            try:
                raw, _ = await asyncio.wait_for(process.communicate(), self.timeout)
                code = process.returncode
            except TimeoutError:
                process.kill()
                raw, code = b"[command timed out]", 124
        finally:
            self.timeout = saved
        ref = self.memory.artifacts.put(raw)
        output, truncated = clip(raw.decode(errors="replace"))
        await self.refresh()
        return dict(code=code, output=output, truncated=truncated, original=ref)

    # -- verification ----------------------------------------------------------

    async def diff(self):
        await self.git("add", "-A")
        exported = await self.git("diff", "--cached", "--binary", "HEAD")
        return self.memory.artifacts.get(exported["original"]).decode()

    async def verify(self, result):
        return await self.check(await self.diff())

    async def check(self, patch):
        """Resolved iff every FAIL_TO_PASS and PASS_TO_PASS test passes in a fresh container."""
        spec = self.spec
        # A new directory per check: Docker Desktop's file sharing can serve a
        # rewritten file's old contents to the next container that mounts it.
        evaluation = self.workspace.parent / f"{self.run_id}-eval-{uuid.uuid4().hex[:8]}"
        evaluation.mkdir(parents=True)
        (evaluation / "model.diff").write_text(patch)
        (evaluation / "test.diff").write_text(spec["test_patch"])
        wanted = spec["FAIL_TO_PASS"] + spec["PASS_TO_PASS"]
        (evaluation / "tests.json").write_text(json.dumps(wanted))
        (evaluation / "run.py").write_text(RUN_TESTS)
        test_files = sorted(set(re.findall(r"^diff --git a/(\S+) ", spec["test_patch"], re.M)))
        reset = " ".join(
            f"(git checkout HEAD -- {shlex.quote(f)} 2>/dev/null || rm -f {shlex.quote(f)});"
            for f in test_files
        )
        apply_model = (
            "git apply --whitespace=nowarn /eval/model.diff || exit 90" if patch.strip() else "true"
        )
        script = (
            f"{ACTIVATE} && cd /testbed && {apply_model} && {reset} "
            "git apply --whitespace=nowarn /eval/test.diff || exit 91; python /eval/run.py"
        )
        outcome = await self.docker(
            "run", "--rm", "--platform", PLATFORM,
            "-v", f"{evaluation.resolve()}:/eval:ro",
            spec["image"], "bash", "-c", script,
            timeout=900,
        )  # fmt: skip
        log = self.memory.artifacts.get(outcome["original"]).decode(errors="replace")
        seen = statuses(log)
        passing = {"PASSED", "XFAIL"}
        failed_f2p = [t for t in spec["FAIL_TO_PASS"] if seen.get(t) not in passing]
        failed_p2p = [t for t in spec["PASS_TO_PASS"] if seen.get(t) not in passing]
        if outcome["code"] == 90:
            reason = "The agent's patch does not apply to a clean checkout"
        elif outcome["code"] == 91:
            reason = "The task's test patch does not apply after the agent's patch"
        else:
            reason = None
        resolved = reason is None and not failed_f2p and not failed_p2p
        return dict(
            status="verified" if resolved else "failed",
            reason=reason,
            fail_to_pass=dict(total=len(spec["FAIL_TO_PASS"]), failing=failed_f2p),
            pass_to_pass=dict(total=len(spec["PASS_TO_PASS"]), failing=failed_p2p),
            patch_bytes=len(patch),
            log=outcome["original"],
        )

    async def export(self):
        data = dict(
            model_name_or_path="econocontext",
            instance_id=self.fixture,
            model_patch=await self.diff(),
        )
        return self.memory.artifacts.put((json.dumps(data) + "\n").encode())
