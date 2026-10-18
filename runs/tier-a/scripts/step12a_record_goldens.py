"""Step 12a: record the equivalence goldens from the SQLite Owner (before the move onto the agent DB).
Each scenario of tests/econocontext/scenarios.py is played through our harness; the requests sent to the
model, the decision log (no timestamps) and the report go to tests/econocontext/golden/<scenario>.json.
No network. Use: PYTHONPATH=. .venv/bin/python runs/tier-a/scripts/step12a_record_goldens.py [--write]"""

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

from tests.econocontext.scenarios import SCENARIOS, play

OUT = Path("tests/econocontext/golden")
for name in SCENARIOS:
    with tempfile.TemporaryDirectory() as tmp:
        got = play(name, Path(tmp))
    fired = Counter(f"{d['rule']}: {d['action']}" + (" (forced)" if d["forced"] else "") for d in got["decisions"])
    print(f"{name}: end={got['end']} requests={len(got['sent'])} decisions={dict(fired)}")
    if "--write" in sys.argv:
        (OUT / f"{name}.json").write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
