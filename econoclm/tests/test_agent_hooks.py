"""EconoCLM's step hook, with a fake sandbox and a fake CLM step."""

import asyncio
import hashlib
import shlex
from dataclasses import dataclass
from pathlib import Path

import pytest
from clm_harness.context_env.types import StepResult

from econoclm.arms.econo_clm.hooks import SETUP_COMMAND, EconoHooks
from econoclm.core.gateway_ledger import Ledger
from econoclm.core.run_store import RunStore


def count(msgs):
    return sum(len(m.get("content") or "") for m in msgs)


@dataclass
class Res:
    stdout: str | None = None
    stderr: str | None = None
    return_code: int = 0


class FakeEnv:
    """A sandbox as a dict of path -> bytes. Understands the few commands the hook runs."""

    def __init__(self, files=None, fail_upload=False):
        self.fs: dict[str, bytes] = dict(files or {})
        self.commands: list[str] = []
        self.fail_upload = fail_upload

    async def upload_file(self, src, dst):
        if self.fail_upload:
            raise RuntimeError("upload broke")
        self.fs[dst] = Path(src).read_bytes()

    async def download_file(self, src, dst):
        if src not in self.fs:
            raise FileNotFoundError(src)
        Path(dst).write_bytes(self.fs[src])

    async def exec(self, command, timeout_sec=None, **kw):
        self.commands.append(command)
        if command.startswith("printf"):
            return Res(stdout="/app\n")  # the `cat <state>/cwd` part
        if command.startswith("sha1sum"):
            out = []
            for p in shlex.split(command)[2:-1]:  # drop "sha1sum --" and "2>/dev/null"
                if p in self.fs:
                    out.append(f"{hashlib.sha1(self.fs[p]).hexdigest()}  {p}")
            return Res(stdout="\n".join(out) + "\n")
        return Res(stdout="")


def make_step(outputs, edits=None):
    """A fake CLM ContextEnv.step: returns the scripted output; on turns listed in
    `edits` it rewrites messages in place (like CLM's parse_back) and says so."""
    edits = edits or {}
    calls = {"n": 0}

    async def step(command, messages, *, environment, pending=None):
        calls["n"] += 1
        n = calls["n"]
        out = outputs[n - 1]
        changed = n in edits
        if changed:
            messages[:] = edits[n](messages)
        block = out if len(out) <= 50 else out[:20] + "...cut..." + out[-20:]
        return StepResult(result=Res(stdout=out), ctx_changed=changed,
                          stdout_block=f"{block}\n\n(exit_code=0)",
                          readout="\n[context: ~1/32000 tokens]",
                          notes="\n[LIVE_CTX_MAIN.txt: edit applied]" if changed else "",
                          exec_time=0.1, touched_ctx=changed)
    return step


def hooks(tmp_path, gateway_db=None):
    return EconoHooks(tmp_path / "run", "econo-t-r1", observation_max_chars=50,
                      protect=lambda: 2, state_dir="/tmp/.bash_ctx_state",
                      gateway_db=gateway_db, count=count)


def base_messages():
    return [{"role": "system", "content": "s" * 100}, {"role": "user", "content": "t" * 100}]


def run(coro):
    return asyncio.run(coro)


def test_tags_count_up_and_outputs_are_uploaded(tmp_path):
    h, env = hooks(tmp_path), FakeEnv()
    step = make_step(["hello\n", "x" * 200])
    msgs = base_messages()

    async def go():
        await h.setup(env)
        a = await h.step(step, "echo hello", msgs, environment=env)
        b = await h.step(step, "python gen.py", msgs, environment=env)
        return a, b
    a, b = run(go())
    assert env.commands[0] == SETUP_COMMAND
    assert a.render().startswith("[obs 1]\nhello")
    assert b.render().startswith("[obs 2] (cut in context; full: econo get 2)\n")
    assert env.fs["/tmp/econo/obs/1.txt"] == b"hello\n"
    assert env.fs["/tmp/econo/obs/2.txt"] == b"x" * 200
    assert (tmp_path / "run/obs/2.txt").read_bytes() == b"x" * 200
    rows = RunStore(tmp_path / "run/econo.sqlite").rows("SELECT * FROM observations")
    assert [(r["obs_id"], r["cut_in_context"]) for r in rows] == [(1, 0), (2, 1)]
    index_cmds = [c for c in env.commands if c.startswith("printf")]
    assert "1\t1\t6\techo hello" in index_cmds[0]
    # status line after every command, after CLM's readout
    assert "\n[context: ~1/32000 tokens]\n[econo] " in a.render()
    assert "| stored: 2" in b.readout


def test_quote_only_when_ctx_changed(tmp_path):
    h, env = hooks(tmp_path), FakeEnv()

    def compact(messages):
        return messages[:2] + [{"role": "user", "content": "summary"}]
    step = make_step(["a\n", "b\n", ""], edits={3: compact})
    msgs = base_messages()

    async def go():
        out = []
        for i, cmd in enumerate(["ls", "ls -la", "python3 edit.py"]):
            out.append(await h.step(step, cmd, msgs, environment=env))
            if i < 2:  # the agent appends the assistant turn and the tool result
                msgs.append({"role": "assistant", "content": "thought " * 10})
                msgs.append({"role": "user", "content": out[-1].render()})
        return out
    out = run(go())
    assert "[econo] edit:" not in out[0].render() and "[econo] edit:" not in out[1].render()
    assert "[econo] edit:" in out[2].notes
    assert "removed: obs 1, 2 (stored: econo get 1)" in out[2].notes
    edits = RunStore(tmp_path / "run/econo.sqlite").rows("SELECT * FROM edits")
    assert len(edits) == 1 and edits[0]["first_change_msg"] == 2


def test_exception_returns_original_result_and_logs(tmp_path):
    h, env = hooks(tmp_path), FakeEnv(fail_upload=True)
    step = make_step(["hello\n"])
    original = {}

    async def wrapped(command, messages, *, environment, pending=None):
        sr = await step(command, messages, environment=environment, pending=pending)
        original["text"] = sr.render()
        original["sr"] = sr
        return sr
    got = run(h.step(wrapped, "echo hello", base_messages(), environment=env))
    assert got is original["sr"]
    assert got.render() == original["text"]  # untouched
    errs = RunStore(tmp_path / "run/econo.sqlite").rows("SELECT * FROM hook_errors")
    assert len(errs) == 1 and "upload broke" in errs[0]["error"]
    assert h.n_hook_errors == 1


def test_stale_file_is_flagged_while_its_output_is_in_context(tmp_path):
    h = hooks(tmp_path)
    env = FakeEnv({"/app/parser.py": b"v1"})
    step = make_step(["v1", "ok\n", "ok\n"])
    msgs = base_messages()

    async def go():
        a = await h.step(step, "cat parser.py", msgs, environment=env)
        msgs.append({"role": "user", "content": a.render()})
        b = await h.step(step, "true", msgs, environment=env)
        env.fs["/app/parser.py"] = b"v2"   # changed after the read
        c = await h.step(step, "true", msgs, environment=env)
        return a, b, c
    a, b, c = run(go())
    assert "stale" not in a.readout and "stale" not in b.readout
    assert c.readout.endswith("| stale: parser.py")
    files = RunStore(tmp_path / "run/econo.sqlite").rows("SELECT * FROM files")
    assert [(f["path"], f["obs_id"]) for f in files] == [("/app/parser.py", 1)]


def test_stale_flag_drops_when_output_left_context(tmp_path):
    h = hooks(tmp_path)
    env = FakeEnv({"/app/parser.py": b"v1"})
    step = make_step(["v1", "ok\n"])
    msgs = base_messages()  # the read's [obs 1] tag is never added to the context

    async def go():
        await h.step(step, "cat parser.py", msgs, environment=env)
        env.fs["/app/parser.py"] = b"v2"
        return await h.step(step, "true", msgs, environment=env)
    assert "stale" not in run(go()).readout


def test_usage_falls_back_to_gateway_ledger(tmp_path):
    db = tmp_path / "gateway.sqlite"
    Ledger(db).insert("econo-t-r1", prompt_tokens=1000, cached_tokens=700, uncached_tokens=300,
                      output_tokens=50, reasoning_tokens=0, cost_usd=0.001)
    h = hooks(tmp_path, gateway_db=db)

    class Usage:  # litellm usage without prompt_tokens_details
        def model_dump(self):
            return {"prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050}

    class Response:
        usage = Usage()
    run(h.on_response(Response(), base_messages()))
    assert h.last.cached == 700 and h.last.uncached == 300
    assert h.k == pytest.approx(1000 / 200)
    assert h.run_cost() == pytest.approx(0.001)


def test_agent_subclass_reads_run_id(tmp_path):
    from econoclm.arms.econo_clm.agent import EconoClmAgent
    agent = EconoClmAgent(logs_dir=tmp_path / "logs", model_name="openai/x",
                          api_base="http://127.0.0.1:8787/run/econo-task-r1/v1",
                          cost_metric="usd", context_budget_tokens=32000,
                          econo_run_dir=str(tmp_path / "runs/econo-task-r1"))
    assert agent._econo.run_id == "econo-task-r1"
    assert agent._econo.gateway_db == tmp_path / "runs/gateway.sqlite"
    assert (tmp_path / "runs/econo-task-r1/econo.sqlite").exists()
