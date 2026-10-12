"""The Owner's behaviour on scripted conversations (tests/econocontext/scenarios.py) is pinned: with the served
model's exact token counts it sends the model exactly the messages, and writes exactly the decision log and
report, in golden/.

History of golden/:
- 12a: recorded from the SQLite Owner with characters / 4; the Postgres Owner reproduced them exactly with
  characters / 4 injected (commit 6c1eb7a).
- 12b (now): the Owner with exact counts and three fixes (a preview's or slice's size includes its note line;
  a capped output is sized as sent; the eviction price measures the prompt after the item). What each change
  did is in runs/tier-a/step12b-2-owner-changes.out."""

import json
from pathlib import Path

import pytest

from tests.econocontext.scenarios import SCENARIOS, play

GOLDEN = Path(__file__).with_name("golden")


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_owner_reproduces_its_goldens(name, tmp_path, agentdb):
    got = json.loads(json.dumps(play(name, tmp_path, dsn=agentdb), sort_keys=True))
    golden = json.loads((GOLDEN / f"{name}.json").read_text())
    assert got["sent"] == golden["sent"]
    assert got["decisions"] == golden["decisions"]
    assert got == golden
