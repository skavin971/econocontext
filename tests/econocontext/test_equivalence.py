"""The move onto the agent DB changes nothing (step 12a): on scripted conversations (tests/econocontext/
scenarios.py), the Owner on Postgres sends the model exactly the messages, and writes exactly the decision
log, that the SQLite Owner did (golden/, recorded before the move). The old counter, characters / 4, is
given here so that only the store differs; with the model's own tokenizer the counts change, and those
differences are reported separately (runs/tier-a/scripts/step12a_equivalence.py), not tested here."""

import json
import math
from pathlib import Path

import pytest

from tests.econocontext.scenarios import SCENARIOS, play

GOLDEN = Path(__file__).with_name("golden")


def chars_over_4(text: str) -> int:
    """The counter before step 12a (econocontext/tokens.py, removed): for this comparison only."""
    return math.ceil(len(text) / 4) if text else 0


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_postgres_owner_reproduces_the_sqlite_owner(name, tmp_path, agentdb):
    got = json.loads(json.dumps(play(name, tmp_path, dsn=agentdb, count=chars_over_4), sort_keys=True))
    golden = json.loads((GOLDEN / f"{name}.json").read_text())
    assert got["sent"] == golden["sent"]
    assert got["decisions"] == golden["decisions"]
    assert got == golden
