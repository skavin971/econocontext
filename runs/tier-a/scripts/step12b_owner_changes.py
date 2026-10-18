"""Step 12b: what the three Owner fixes change on the scripted conversations. Compares, per scenario, three
results (each JSON from step12b_play.py, or the goldens): A = the goldens before 12b (characters / 4, the
SQLite-era behaviour); B = the Owner at 6c1eb7a with exact counts (no fix); C = the fixed Owner with exact
counts (the new goldens). A -> B is the counts alone; B -> C is the fixes alone.
Use: PYTHONPATH=. .venv/bin/python runs/tier-a/scripts/step12b_owner_changes.py <A dir> <B dir> <C dir>"""

import json
import sys
from pathlib import Path

CHOICE = ("call_no", "rule", "item_id", "action", "forced")
A, B, C = map(Path, sys.argv[1:4])


def load(d, name):
    return json.loads((d / f"{name}.json").read_text())


def choice(d):
    return tuple(d[k] for k in CHOICE)


def show(x):
    return f"{x['call_no']}:{x['rule']} {x['action']}" + (f" item {x['item_id']}" if x["item_id"] else "") + \
        (" forced" if x["forced"] else "")


def compare(title, a, b):
    print(f"  {title}:")
    da, db = a["decisions"], b["decisions"]
    if [choice(x) for x in da] != [choice(x) for x in db]:
        print("    decisions change:")
        print("      before: " + "; ".join(map(show, da)))
        print("      after:  " + "; ".join(map(show, db)))
    else:
        changed = [(x, y) for x, y in zip(da, db) if x != y]
        print(f"    the same {len(da)} decisions" + (f"; numbers change in {len(changed)}:" if changed else "; identical"))
        for x, y in changed:
            keys = [k for k in ("prices", "note") if x[k] != y[k]]
            detail = "; ".join(f"{k} {json.dumps(x[k])} -> {json.dumps(y[k])}" for k in keys)
            print(f"      {show(x)}: {detail}")
    diff = [i for i, (x, y) in enumerate(zip(a["sent"], b["sent"])) if x != y]
    if len(a["sent"]) != len(b["sent"]) or diff:
        i = diff[0] if diff else min(len(a["sent"]), len(b["sent"]))
        print(f"    messages to the model: {len(a['sent'])} -> {len(b['sent'])} requests; first change in request {i + 1}")
        if diff:
            for j, (x, y) in enumerate(zip(a["sent"][i], b["sent"][i])):
                if x != y:
                    print(f"      message {j} ({x.get('role')}): {str(x.get('content'))[:110]!r}")
                    print(f"      {' ' * (len(str(j)) + len(str(x.get('role'))) + 12)}-> {str(y.get('content'))[:110]!r}")
                    break
    else:
        print("    messages to the model: identical")


for path in sorted(C.glob("*.json")):
    name = path.stem
    print(f"\n## {name}")
    compare("A -> B, exact counts alone", load(A, name), load(B, name))
    compare("B -> C, the three fixes", load(B, name), load(C, name))
