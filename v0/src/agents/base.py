"""Plumbing every agent needs, and none of it domain-specific.

Staging the caller's data, keeping tool paths inside the workspace, running a
subprocess, and telling the store which sources changed. What tools exist, what
the agent is told, and what counts as done belong to each agent.
"""

import asyncio
import difflib
import os
import signal
import sys
from hashlib import sha256
from pathlib import Path

from econocontext.agent import STRING, controls, tool
from econocontext.contracts import canonical

MAX_FILES = 200
MAX_BYTES = 200_000
READ_BUDGET = 16_000

# Read and search are the floor: an agent that cannot look at its data is not an
# agent. Everything beyond this is the individual agent's to declare.
SHARED_TOOLS = [
    tool("search", "Search task files and return matching passages.", {"query": STRING}, ["query"]),
    tool("read", "Read an exact task file and its evidence reference.", {"path": STRING}, ["path"]),
]


class BaseAgent:
    """Implements the DomainAdapter protocol except prompt, tools and verify."""

    name = "base"
    fixtures = Path(__file__).parent

    def __init__(self, task, workspace, memory, run_id, timeout):
        self.task, self.workspace, self.memory, self.run_id = task, workspace, memory, run_id
        self.timeout = timeout
        self.baseline = {}
        self.processes = set()
        # Caller-supplied data means no fixture is synthesized.
        self.fixture = None if task.data else (task.fixture or self.default_fixture)
        self.goal = task.prompt or (self.fixture_goal(self.fixture) if self.fixture else "")
        if not self.goal:
            raise ValueError("A task with your own data requires a prompt")
        self.delegation_tool = False

    # -- fixtures ---------------------------------------------------------

    def fixture_root(self, name):
        return self.fixtures / "fixtures" / name

    def fixture_goal(self, name):
        return (self.fixture_root(name) / "GOAL.md").read_text().strip()

    # -- preparation ------------------------------------------------------

    async def prepare(self):
        self.workspace.mkdir(parents=True, exist_ok=True)
        if self.task.data:
            await self.stage(Path(self.task.data).resolve())
        else:
            source = self.fixture_root(self.fixture) / "task"
            for item in sorted(source.rglob("*")):
                if item.is_file():
                    target = self.workspace / item.relative_to(source)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(item.read_bytes())
        for path in self.files():
            self.baseline[str(path.relative_to(self.workspace))] = path.read_text()
        await self.refresh()
        return self.memory.artifacts.json(self.baseline)

    async def stage(self, source):
        """Put the caller's material in the run workspace, never editing it in place.

        A pinned repository is cloned and detached so the commit is exact. A plain
        directory is snapshot-copied under the same size caps the tools use, so a
        failed run leaves the caller's files untouched and the exported patch is a
        clean diff against what the agent actually saw.
        """
        if not source.is_dir():
            raise ValueError(f"Task data is not a directory: {source}")
        if self.task.commit:
            resolved = await self.command(
                ["git", "rev-parse", "--verify", self.task.commit + "^{commit}"], cwd=source
            )
            if resolved["code"]:
                raise ValueError("Invalid pinned commit")
            await self.command(
                ["git", "clone", "--no-hardlinks", str(source), str(self.workspace / "repo")]
            )
            self.workspace = self.workspace / "repo"
            checked = await self.command(["git", "checkout", "--detach", resolved["output"].strip()])
            if checked["code"]:
                raise ValueError("Pinned checkout failed")
            return
        staged = 0
        for item in sorted(source.rglob("*")):
            relative = item.relative_to(source)
            if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
                continue
            if not item.is_file() or item.is_symlink() or item.stat().st_size > MAX_BYTES:
                continue
            target = self.workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item.read_bytes())
            staged += 1
            if staged >= MAX_FILES:
                break
        if not staged:
            raise ValueError(f"No readable files under {source}")

    # -- workspace --------------------------------------------------------

    def path(self, name):
        result = (self.workspace / name).resolve()
        if (
            not result.is_relative_to(self.workspace.resolve())
            or ".git" in result.relative_to(self.workspace.resolve()).parts
        ):
            raise PermissionError("Path outside task workspace")
        return result

    def files(self):
        return [
            p
            for p in sorted(self.workspace.rglob("*"))
            if p.is_file()
            and not p.is_symlink()
            and not any(
                x.startswith(".") or x == "__pycache__" for x in p.relative_to(self.workspace).parts
            )
            and p.stat().st_size <= MAX_BYTES
        ][:MAX_FILES]

    async def refresh(self):
        """Tell the store which sources changed. Staleness detection rests on this."""
        current = await self.memory.versions(self.run_id)
        for path in self.files():
            source = str(path.relative_to(self.workspace))
            try:
                text = path.read_text()
            except UnicodeDecodeError:
                continue
            version = sha256(text.encode()).hexdigest()
            if current.get(source) != version:
                await self.memory.observe(self.run_id, source, text)
        # Missing files invalidate their previous source bindings.
        present = {str(p.relative_to(self.workspace)) for p in self.files()}
        for source in current:
            if not source.startswith("tool:") and source not in present:
                await self.memory.write(
                    "DELETE FROM bindings WHERE run_id=? AND source=?", (self.run_id, source)
                )

    async def command(self, argv, cwd=None):
        # These processes execute trusted code; path checks are not an OS sandbox.
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd or self.workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
            env={
                k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "TMPDIR", "SYSTEMROOT")
            },
        )
        self.processes.add(process)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), self.timeout)
            ref = self.memory.artifacts.put(output)
            return dict(
                code=process.returncode,
                output=output.decode(errors="replace")[:READ_BUDGET],
                original=ref,
                truncated=len(output) > READ_BUDGET,
            )
        finally:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
            self.processes.discard(process)

    # -- tools every agent has --------------------------------------------

    def tools(self, worker, method):
        return list(SHARED_TOOLS) + controls(worker, method, self.delegation_tool)

    async def execute(self, name, arguments, worker):
        if name == "read":
            path = self.path(arguments["path"])
            text = path.read_text()
            evidence = await self.memory.observe(self.run_id, arguments["path"], text)
            return dict(
                text=text[:READ_BUDGET],
                evidence=[evidence.id],
                truncated=len(text) > READ_BUDGET,
                original=evidence.payload,
            )
        if name == "search":
            query = arguments["query"].lower()
            matches = []
            for path in self.files():
                text = path.read_text()
                if query in text.lower() or query in path.name.lower():
                    evidence = await self.memory.observe(
                        self.run_id, str(path.relative_to(self.workspace)), text
                    )
                    matches.append(
                        dict(
                            path=str(path.relative_to(self.workspace)),
                            excerpt=text[:2000],
                            evidence=evidence.id,
                        )
                    )
                if len(matches) == 8:
                    break
            return dict(matches=matches)
        raise ValueError("Unknown domain tool")

    async def run_verify_script(self):
        """Run the fixture's hidden verifier, if it has one."""
        script = self.fixture_root(self.fixture) / "verify.py"
        if not script.exists():
            return None
        outcome = await self.command([sys.executable, "-c", script.read_text()])
        return dict(status="verified" if outcome["code"] == 0 else "failed", details=outcome)

    async def export(self):
        return None

    async def export_patch(self, instance_default):
        """A unified diff of everything the agent changed, against what it was given."""
        if self.task.commit:
            exported = await self.command(["git", "diff", "--binary", "HEAD"])
            patch = self.memory.artifacts.get(exported["original"]).decode()
        else:
            patch = "".join(
                "".join(
                    difflib.unified_diff(
                        old.splitlines(True),
                        self.path(name).read_text().splitlines(True),
                        fromfile="a/" + name,
                        tofile="b/" + name,
                    )
                )
                for name, old in self.baseline.items()
            )
        data = dict(
            model_name_or_path="econocontext",
            instance_id=self.task.instance_id or instance_default,
            model_patch=patch,
        )
        return self.memory.artifacts.put((canonical(data) + "\n").encode())
