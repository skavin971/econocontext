"""Evidence identity: the same path at another version is another piece of evidence."""

from econocontext.evidence import EvidenceEvent, file_version, make_ref, normalize_path
from econocontext.store.db import AgentDB


def test_identity_is_source_version_and_range_not_the_path():
    v5 = make_ref("file", "auth.py", "sha256:aaa", "def login(): ...")
    again = make_ref("file", "auth.py", "sha256:aaa", "def login(): ...")
    v6 = make_ref("file", "auth.py", "sha256:bbb", "def login(): ...")
    part = make_ref("file", "auth.py", "sha256:aaa", "def", range='{"offset": 120}')
    assert v5.evidence_id == again.evidence_id
    assert len({v5.evidence_id, v6.evidence_id, part.evidence_id}) == 3
    assert v5.byte_size == 16 and v5.token_size > 0 and v5.recoverable


def test_a_file_version_is_its_bytes_and_changes_with_an_edit(tmp_path):
    (tmp_path / "auth.py").write_text("v5")
    before = file_version(str(tmp_path), "auth.py")
    (tmp_path / "auth.py").write_text("v6")
    assert before.startswith("sha256:") and file_version(str(tmp_path), "auth.py") != before
    assert file_version(str(tmp_path), "missing.py") is None and file_version(None, "a") is None


def test_paths_are_repo_relative_inside_the_workspace(tmp_path):
    work = str(tmp_path)
    assert normalize_path("src/../auth.py", work) == "auth.py"
    assert normalize_path(f"{tmp_path}/pkg/a.py", work) == "pkg/a.py"
    assert normalize_path("/etc/hosts", work) == "/etc/hosts"
    assert normalize_path("./a.py", None) == "a.py"


def test_evidence_rows_are_written_once_and_events_in_order(tmp_path):
    db = AgentDB(tmp_path / "db.sqlite3")
    ref = make_ref("file", "a.py", "sha256:1", "x")
    read = EvidenceEvent("acquired", "read_file", "k", "a.py", ref)
    db.add_evidence_events("r", "r:root", 1, [read, EvidenceEvent("mutated", "replace", "w", "a.py")])
    db.add_evidence_events("r", "r:root", 3, [read])
    assert db.rows("SELECT COUNT(*) FROM evidence WHERE run_id='r'")[0][0] == 1
    events = [tuple(r) for r in db.rows(
        "SELECT seq, call_no, event, evidence_id IS NULL FROM evidence_events ORDER BY seq")]
    assert events == [(0, 1, "acquired", 0), (1, 1, "mutated", 1), (2, 3, "acquired", 0)]
    assert db.mutations("r") == 1
