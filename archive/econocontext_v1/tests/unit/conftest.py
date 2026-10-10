"""Shared plain-data fixtures for unit tests. No LLM, no network, no Docker."""

from pathlib import Path

import pytest

from econocontext import config
from econocontext.engine import EconoContext, make_segment
from econocontext.host import HostCapabilities
from econocontext.types import SegmentKind

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


@pytest.fixture
def cfg():
    return config.load(CONFIG_DIR)


class FakeHost:
    """A host double with every capability and an in-memory pointer store (plain data)."""
    def __init__(self):
        self.capabilities = HostCapabilities(pointer=True, answer_from_store=True,
                                             reuse_result=True, edit_request=True)
        self.saved = {}
        self.pointer_store = self

    def materialize(self, segment):
        path = f"/tmp/econocontext/{segment.id[:12]}.txt"
        self.saved[path] = segment.text
        return path


@pytest.fixture
def engine(tmp_path):
    def build(mode="observe", host=None, **kw):
        return EconoContext(str(CONFIG_DIR), host or FakeHost(), "run-1", host_name="test",
                            arm="econo", db_path=str(tmp_path / "db.sqlite3"), mode=mode, **kw)
    return build


def seg(agent, native, kind, text, role="user", **kw):
    return make_segment("run-1", agent, native, kind, text, role=role, **kw)


def conversation(agent="run-1:root"):
    """system, task, an assistant tool call, and its result: a minimal valid window."""
    return [
        seg(agent, "sys", SegmentKind.SYSTEM, "You are a coding agent.", role="system"),
        seg(agent, "task", SegmentKind.TASK, "Fix the bug in src/app.py parse_items", role="user"),
        seg(agent, "ai1", SegmentKind.TOOL_CALL, 'read_file {"file_path": "/testbed/src/app.py"}',
            role="assistant", pair_id="c1"),
        seg(agent, "c1", SegmentKind.TOOL_RESULT, "def parse_items(x):\n    return x\n", role="tool",
            pair_id="c1", source="/testbed/src/app.py"),
    ]
