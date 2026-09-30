"""Evidence labels: a repeated read is redundant only at the same version, with no write between."""

from econocontext.evidence import EvidenceEvent, make_ref
from econocontext.learn.evidence_labels import evidence_summary, label_evidence
from econocontext.learn.labels import label_run
from econocontext.store.db import AgentDB


def read(path, version, tool="read_file"):
    return EvidenceEvent("acquired", tool, f"{tool}:{path}", path,
                         make_ref("file", path, version, f"{path}@{version}"))


def write(path):
    return EvidenceEvent("mutated", "replace", f"replace:{path}", path)


def search(key, epoch):
    return EvidenceEvent("acquired", "grep_search", key, key, make_ref("search", key, f"epoch:{epoch}", "hits"))


def labelled(tmp_path, calls: list[list[EvidenceEvent]]) -> list[dict]:
    db = AgentDB(tmp_path / "db.sqlite3")
    db.start_run("r", "omnigent:gemini", None, "econo", "observe", "m", 1.0, "f")
    for call_no, events in enumerate(calls):
        db.add_evidence_events("r", "r:root", call_no, events)
    label_evidence(db, "r")
    return evidence_summary(db, "r")


def test_the_same_version_read_again_is_a_redundant_reacquisition(tmp_path):
    first, second = labelled(tmp_path, [[read("auth.py", "v5")], [], [read("auth.py", "v5")]])
    assert (first["refetched"], first["reacquired_same_version"],
            first["reacquired_after_mutation"]) == (1, 1, 0)
    assert (first["next_use_call"], first["calls_until_next_use"], first["number_future_uses"]) == (2, 2, 1)
    assert not second["was_used_again"] and second["reacquired_same_version"] is None


def test_a_read_after_an_edit_is_not_redundant(tmp_path):
    first, _ = labelled(tmp_path, [[read("auth.py", "v5")], [write("auth.py")], [read("auth.py", "v6")]])
    assert (first["reacquired_same_version"], first["reacquired_after_mutation"]) == (0, 1)


def test_a_shell_command_may_have_changed_anything(tmp_path):
    shell = EvidenceEvent("mutated", "run_shell_command", "sh", "*")
    # Same bytes afterwards, but a write came between: not counted as redundant.
    first, _ = labelled(tmp_path, [[read("a.py", "v1")], [shell], [read("a.py", "v1")]])
    assert (first["reacquired_same_version"], first["reacquired_after_mutation"]) == (0, 1)


def test_a_write_elsewhere_does_not_matter_to_a_file_but_does_to_a_search(tmp_path):
    rows = labelled(tmp_path, [[read("a.py", "v1"), search("grep:x", 0)], [write("b.py")],
                               [read("a.py", "v1"), search("grep:x", 1)]])
    assert rows[0]["reacquired_same_version"] == 1   # a.py: b.py's edit is irrelevant
    assert rows[1]["reacquired_after_mutation"] == 1  # grep: any edit may change its hits


def test_future_uses_are_counted_and_other_labels_survive(tmp_path):
    db = AgentDB(tmp_path / "db.sqlite3")
    db.start_run("r", "omnigent:gemini", None, "econo", "observe", "m", 1.0, "f")
    for call_no in (0, 3, 4):
        db.add_evidence_events("r", "r:root", call_no, [read("a.py", "v1")])
    label_run(db, "r")  # replaces all of a run's labels, so evidence labels come after it
    assert label_evidence(db, "r") == dict(run_id="r", acquisitions=3, reacquired=2,
                                           same_version=2, after_mutation=0)
    first = evidence_summary(db, "r")[0]
    assert first["needed_calls"] == [3, 4] and first["number_future_uses"] == 2


def test_another_range_of_the_same_file_is_not_redundant(tmp_path):
    def part(start):
        return EvidenceEvent("acquired", "read_file", f"r{start}", "a.py",
                             make_ref("file", "a.py", "v1", "x", range=f'{{"start_line": {start}}}'))
    first, second, third = labelled(tmp_path, [[part(1)], [part(50)], [part(50)]])
    assert first["refetched"] == 1 and first["reacquired_same_version"] == 0
    assert second["reacquired_same_version"] == 1  # the same range again is
