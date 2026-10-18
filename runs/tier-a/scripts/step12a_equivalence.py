"""Step 12a, the equivalence check's second half: the scripted conversations (tests/econocontext/scenarios.py)
on the Postgres Owner with the served model's exact token counts (measure/tokens.py count_text), against the
goldens recorded from the SQLite Owner with characters / 4. The first half (the same Owner with characters / 4
reproduces every golden exactly) is tests/econocontext/test_equivalence.py. Every difference here comes from
the counts alone; they are listed, not failed. No network: Postgres from pgserver, a scripted model.
Use: PYTHONPATH=. .venv/bin/python runs/tier-a/scripts/step12a_equivalence.py"""

import json
import math
import tempfile
from pathlib import Path

import pgserver
import psycopg

from measure.tokens import count_text
from tests.econocontext.scenarios import SCENARIOS, play

GOLDEN = Path("tests/econocontext/golden")
CHOICE = ("call_no", "rule", "item_id", "action", "forced")


def chars_over_4(text):
    return math.ceil(len(text) / 4) if text else 0


def fresh(uri):
    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    return uri


def choices(decisions):
    return [tuple(d[k] for k in CHOICE) for d in decisions]


with tempfile.TemporaryDirectory() as tmp:
    server = pgserver.get_server(Path(tmp) / "pg")
    uri = server.get_uri()
    totals = {"identical": 0, "different": 0}
    for name in SCENARIOS:
        golden = json.loads((GOLDEN / f"{name}.json").read_text())
        with tempfile.TemporaryDirectory() as t:
            control = json.loads(json.dumps(play(name, Path(t), dsn=fresh(uri), count=chars_over_4), sort_keys=True))
        with tempfile.TemporaryDirectory() as t:
            exact = json.loads(json.dumps(play(name, Path(t), dsn=fresh(uri)), sort_keys=True))
        assert control == golden, f"{name}: the store alone changed something (see test_equivalence.py)"
        print(f"\n## {name}")
        print("- characters / 4 on Postgres: identical to the golden (messages, decisions, report)")
        same = exact == golden
        totals["identical" if same else "different"] += 1
        if same:
            print("- exact counts: identical to the golden")
            continue
        g, e = golden["decisions"], exact["decisions"]
        if choices(g) == choices(e):
            print(f"- exact counts: the same {len(g)} decisions (rule, action, item, call); only numbers differ:")
        else:
            print(f"- exact counts: the decisions differ ({len(g)} before, {len(e)} now):")
            for side, rows in (("golden", choices(g)), ("exact ", choices(e))):
                print(f"    {side}: " + "; ".join(f"{c}:{r} {a} item={i}" + (" forced" if f else "")
                                                  for c, r, i, a, f in rows))
        for i, (a, b) in enumerate(zip(g, e)):
            if a != b and tuple(a[k] for k in CHOICE) == tuple(b[k] for k in CHOICE):
                keys = sorted(k for k in a if a[k] != b[k])
                detail = {k: (a[k], b[k]) for k in keys if k == "prices"}
                note = f" note {a['note']} -> {b['note']}" if "note" in keys else ""
                print(f"    decision {i} ({a['rule']} {a['action']}): {', '.join(keys)} changed"
                      + (f"; prices {json.dumps(detail['prices'][0])} -> {json.dumps(detail['prices'][1])}"
                         if detail else "") + note)
        sent_g, sent_e = golden["sent"], exact["sent"]
        changed = [i for i, (a, b) in enumerate(zip(sent_g, sent_e)) if a != b]
        print(f"- messages to the model: {len(sent_e)} requests (golden {len(sent_g)}); "
              f"{'all identical' if not changed and len(sent_g) == len(sent_e) else f'first differing request {changed[:1]}'}")
        for i in changed[:1]:
            for j, (a, b) in enumerate(zip(sent_g[i], sent_e[i])):
                if a != b:
                    print(f"    request {i}, message {j} ({a.get('role')}): golden {str(a.get('content'))[:160]!r}")
                    print(f"    {' ' * (len(str(i)) + len(str(j)) + 20)}exact  {str(b.get('content'))[:160]!r}")
                    break
        if golden["report"] != exact["report"]:
            print(f"- report: {golden['report']['decisions']} -> {exact['report']['decisions']}")
    server.cleanup()
    print(f"\nscenarios identical with exact counts: {totals['identical']}; changed by the counts: {totals['different']}")
