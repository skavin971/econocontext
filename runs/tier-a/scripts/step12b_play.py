"""Step 12b: play every scripted conversation (tests/econocontext/scenarios.py) on the current Owner with exact
counts, on a pgserver Postgres, and save each result (requests sent, decision log, report) as JSON.
Use: PYTHONPATH=. .venv/bin/python runs/tier-a/scripts/step12b_play.py <out dir>"""

import json
import sys
import tempfile
from pathlib import Path

import pgserver
import psycopg

from tests.econocontext.scenarios import SCENARIOS, play

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    server = pgserver.get_server(Path(tmp) / "pg")
    for name in SCENARIOS:
        with psycopg.connect(server.get_uri(), autocommit=True) as conn:
            conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        with tempfile.TemporaryDirectory() as t:
            got = play(name, Path(t), dsn=server.get_uri())
        (out / f"{name}.json").write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
        print(name, len(got["sent"]), "requests,", len(got["decisions"]), "decisions")
    server.cleanup()
