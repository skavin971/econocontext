"""Evidence table for a v2 run: for each rule, did it fire, what did the model see, what did it do next.

Run: .venv/bin/python benchmarks/tblite/evidence.py runs/v2spike/econo+jev/spike-all-nine
"""

import glob
import json
import sqlite3
import sys
from pathlib import Path

RULES = ["1 dont_repeat", "2 delta_read", "3 serve_stored", "4 arrival", "5 worker_report",
         "6 evict", "7 compact", "8 placement", "9 invalidate"]


def transcript_events(trial: Path) -> list[dict]:
    """Main-agent transcript as a flat list: tool calls, tool results (with any EconoContext
    note the model saw), and assistant text."""
    events = []
    for path in sorted(glob.glob(str(trial / "agent" / "sessions" / "projects" / "*" / "*.jsonl"))):
        for line in open(path):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            content = (entry.get("message") or {}).get("content")
            if entry.get("type") == "system" or entry.get("isCompactSummary"):
                events.append({"kind": "compact", "text": json.dumps(entry)[:300]})
            if not isinstance(content, list):
                if isinstance(content, str) and "EconoContext" in content:
                    events.append({"kind": "note", "text": content[:300]})
                continue
            for block in content:
                if block.get("type") == "tool_use":
                    events.append({"kind": "call", "text": f"{block['name']} {json.dumps(block.get('input'))[:160]}"})
                elif block.get("type") == "tool_result":
                    body = block.get("content")
                    if isinstance(body, list):
                        body = " ".join(b.get("text", "") for b in body if isinstance(b, dict))
                    events.append({"kind": "result", "text": str(body)[:200].replace("\n", " | ")})
                elif block.get("type") == "text" and "EconoContext" in block.get("text", ""):
                    events.append({"kind": "note", "text": block["text"][:300]})
    return events


def main(out: Path) -> None:
    rows = []
    for path in sorted((out / "sessions").glob("*.sqlite3")):
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            rows += [dict(r) for r in db.execute("SELECT * FROM decisions ORDER BY id")]
    print(f"# Evidence for {out}\n")
    for rule in RULES:
        mine = [r for r in rows if r["rule"] == rule]
        actions = {}
        for r in mine:
            actions[r["action"]] = actions.get(r["action"], 0) + 1
        print(f"- {rule}: {actions or 'did not fire'}")
        for r in mine[:2]:
            answers = json.loads(r["answers"]) if r["answers"] else {}
            print(f"    call {r['call_no']}: {r['action']}{' (forced)' if r['forced'] else ''} "
                  f"source={answers.get('source', '-')} note={str(r['note'])[:120]}")
    trial = next((out / "harbor").glob("*"), None)
    if trial:
        print("\n## Main transcript (calls, results, EconoContext notes)\n")
        for event in transcript_events(trial):
            print(f"[{event['kind']}] {event['text']}")
    summary = out / "summary.json"
    if summary.exists():
        s = json.loads(summary.read_text())
        print(f"\nreward={s['reward']} run_cost=${s['run_cost_usd']} gateway=${s['gateway_cost_usd']} jev={s['jev']}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
