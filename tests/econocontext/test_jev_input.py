"""Jev's input is built only from agent-DB reads plus the task (step 12a): on a scripted run, every question
the predictor is asked carries exactly the turns and items that the agent DB's window holds at that moment,
and nothing the window does not hold."""

import json
from pathlib import Path

from agent_db import Form, PostgresAgentDB, SegmentKind

from tests.econocontext.scenarios import FixedPredictor, Replies, Scripted, numbered_file, play, SCENARIOS


def from_the_store(store, run):
    """The turns and big items, rebuilt independently from the window alone."""
    turns = []
    for s in store.window(run):
        if not s.part:
            turns.append({"turn": s.position, "role": s.role, **({"text": s.text} if s.text else {})})
        elif s.kind == SegmentKind.TOOL_CALL:
            turns[-1].setdefault("tool_calls", []).append(json.loads(s.text))
    return turns


class Watching(FixedPredictor):
    """Checks each question against the agent DB at the moment it is asked."""
    def __init__(self, owner_ref):
        self.owner_ref, self.checked = owner_ref, []

    def _check(self, task, turns):
        owner = self.owner_ref()
        store = owner.store
        assert task == store.get_run(owner.run)["task"]
        assert turns == from_the_store(store, owner.run)
        self.checked.append(len(turns))

    def arrival(self, task, convo, item):
        self._check(task, convo)
        owner = self.owner_ref()
        stored = owner.store.segment(owner._result_id)
        assert item["text"] == stored.text            # the new result, as stored
        return super().arrival(task, convo, item)

    def segment(self, task, turns, items, boundaries):
        self._check(task, turns)
        owner = self.owner_ref()
        rows = {r["tool_result_id"]: r for r in owner.store.window_results(owner.run)}
        for i in items:                                # each item is a window entry, at its position
            row = rows[str(i["id"])]
            assert (i["turn"], i["tokens"], i["tool"], i["input"]) == \
                (row["position"], row["tokens"], row["tool_name"], row["args"]) and row["form"] != Form.EVICTED
        return super().segment(task, turns, items, boundaries)


def test_every_question_to_jev_is_built_from_the_agent_db(tmp_path, agentdb, monkeypatch):
    watchers = []

    class Policy(Scripted):
        def start(self, instruction, harness):
            super().start(instruction, harness)
            watcher = Watching(lambda: self.owner)
            self.owner.predictor = watcher
            watchers.append(watcher)

    monkeypatch.setattr("tests.econocontext.scenarios.Scripted", Policy)
    play("predictor_rules", tmp_path, dsn=agentdb)
    assert len(watchers[0].checked) >= 3 and all(n > 2 for n in watchers[0].checked)
