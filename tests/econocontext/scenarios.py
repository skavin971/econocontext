"""Scripted conversations for the equivalence check (step 12a): our harness, a scripted model, a local shell
and EconoContext in prior mode (fixed guesses), with forced rules so that every rule fires. play() returns
what the check compares: every request sent to the model and the decision log. No network.

The goldens in golden/ were recorded from the SQLite Owner before the move onto the agent DB
(runs/tier-a/scripts/step12a_record_goldens.py)."""

import asyncio
import json
from types import SimpleNamespace

from openai.types.chat import ChatCompletion

from econocontext.policy import Econo
from harness.agent import Agent
from tests.harness.helpers import FakeModel, LocalEnv

DECISION_FIELDS = ("call_no", "rule", "item_id", "action", "forced", "prices", "answers", "jev_usage", "note")


def numbered_file(lines: int, width: int = 40) -> str:
    return "".join(f"line {i:04d} " + "x" * width + "\n" for i in range(1, lines + 1))


class Replies:
    """Model replies with unique tool-call ids and a chosen prompt size (the Owner reads prompt_tokens)."""
    def __init__(self):
        self.n = 0

    def __call__(self, name=None, args=None, text=None, prompt=1000):
        self.n += 1
        message = {"role": "assistant", "content": text}
        if name:
            message["tool_calls"] = [{"id": f"call_{self.n}", "type": "function",
                                      "function": {"name": name, "arguments": json.dumps(args or {})}}]
        return ChatCompletion.model_validate({
            "id": f"r{self.n}", "object": "chat.completion", "created": 0, "model": "qwen3.8:27b",
            "choices": [{"index": 0, "finish_reason": "tool_calls" if name else "stop", "message": message}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": 50, "total_tokens": prompt + 50}})


class FixedPredictor:
    """Fixed answers that make the unforced rules fire: a slice on arrival, items and segments finished."""
    name = "fixed"

    def arrival(self, task, convo, item):
        return {"needed_again": 0.6, "lifetime": "few_calls", "relevant": {0: 0.6, 2: 0.35}}, None

    def segment(self, task, turns, items, boundaries):
        return {"done": {i["id"]: 0.9 for i in items}, "segment_done": 0.9,
                "segment_end": boundaries[-1] if boundaries else None}, None


def repeats_and_reads(r):
    """Rules 1 (before and after a run), 2, 4 (by price) and 9."""
    return [
        r("write_file", {"path": "data.txt", "content": numbered_file(100, 10)}),
        r("bash", {"command": "cat data.txt"}),
        r("bash", {"command": "cat data.txt"}),
        r("write_file", {"path": "a.py", "content": numbered_file(300)}),
        r("read_file", {"path": "a.py", "start_line": 1, "end_line": 50}),
        r("read_file", {"path": "a.py", "start_line": 40, "end_line": 100}),
        r("bash", {"command": "cat data.txt"}),
        r("bash", {"command": "seq 1 3000"}),
        r("bash", {"command": "wc -l a.py"}),
        r("submit"),
    ]


def evict_and_serve(r):
    """Rule 6 (forced), then rule 3: the evicted output is served again from the store."""
    return [
        r("write_file", {"path": "big.txt", "content": numbered_file(400, 60)}),
        r("bash", {"command": "cat big.txt"}),
        r("bash", {"command": "echo one"}),
        r("bash", {"command": "echo two"}),
        r("bash", {"command": "cat big.txt"}),
        r("bash", {"command": "echo three"}),
        r("submit"),
    ]


def forced_compaction(r):
    """Rules 4 (forced to a preview) and 7 (forced): the model's own summary replaces the segment."""
    script = [r("write_file", {"path": "big.txt", "content": numbered_file(400, 60)}),
              r("bash", {"command": "cat big.txt"})]
    script += [r("bash", {"command": f"echo step {i}"}) for i in range(6)]
    script += [r(text="Found it: big.txt has 400 lines; nothing to change yet.")]    # the summary call
    script += [r("bash", {"command": "cat big.txt"}), r("submit")]
    return script


def predictor_rules(r):
    """A fixed predictor and big prompts: rule 4 as a slice, rule 6 by price, rule 7 by the base rules."""
    script = [r("write_file", {"path": "big.txt", "content": numbered_file(400, 60)}),
              r("bash", {"command": "cat big.txt"}),
              r("read_file", {"path": "big.txt", "start_line": 1, "end_line": 120}),
              r("bash", {"command": "echo step 0"}),
              r("bash", {"command": "echo step 1"}),
              r("bash", {"command": "echo step 2"}, prompt=32_000)]       # the next check may compact
    script += [r(text="Read big.txt twice; it is plain data.", prompt=33_000)]    # the summary call
    script += [r("bash", {"command": "echo after"}, prompt=12_000), r("submit", prompt=12_000)]
    return script


SCENARIOS = {
    "repeats_and_reads": (repeats_and_reads, (), None),
    "evict_and_serve": (evict_and_serve, ("6 evict",), None),
    "forced_compaction": (forced_compaction, ("7 compact", "4 arrival"), None),
    "predictor_rules": (predictor_rules, (), FixedPredictor),
}


class Scripted(Econo):
    """Econo in prior mode, with a fixed predictor put in after the Owner is built (when one is given)."""
    def __init__(self, predictor=None, **kwargs):
        super().__init__(mode="prior", **kwargs)
        self.fixed = predictor

    def start(self, instruction, harness):
        super().start(instruction, harness)
        if self.fixed:
            self.owner.predictor = self.fixed()


def play(name: str, tmp_path, monkeypatch=None, **policy_kwargs) -> dict:
    """Run one scenario; return the requests sent to the model, the decision log and the report."""
    script, force, predictor = SCENARIOS[name]
    model = FakeModel(script(Replies()))
    policy = Scripted(predictor, force=force, **policy_kwargs)
    agent = Agent(logs_dir=tmp_path / "logs", model_name="openai/qwen3.8:27b", client=model,
                  state_dir=str(tmp_path / "state"), policy=policy, run_id=f"golden-{name}")
    env = LocalEnv(tmp_path / "work")
    (tmp_path / "work").mkdir(exist_ok=True)
    context = SimpleNamespace(n_input_tokens=0, n_cache_tokens=0, n_output_tokens=0)

    async def go():
        await agent.setup(env)
        await agent.run("Inspect the files and report.", env, context)
    asyncio.run(go())
    return {"end": agent.end, "sent": model.sent, "decisions": decisions(policy.owner),
            "report": policy.report()}


def decisions(owner) -> list[dict]:
    """The decision log without timestamps, JSON fields parsed, from either store."""
    if hasattr(owner, "decisions"):
        rows = owner.decisions()
    else:   # the SQLite session (before step 12a)
        rows = [dict(row) for row in owner.s.decisions()]
    out = []
    for row in rows:
        d = {k: row.get(k) for k in DECISION_FIELDS}
        for k in ("prices", "answers", "jev_usage"):
            if isinstance(d[k], str):
                d[k] = json.loads(d[k])
        d["forced"] = bool(d["forced"])
        out.append(d)
    return json.loads(json.dumps(out))     # dict keys as JSON has them (item ids become strings)
