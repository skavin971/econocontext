"""Offline research analysis. Reads only the research database, in SQLite read-only mode."""

import argparse
import base64
import json
import sqlite3
from pathlib import Path

from .recorder import APPLICATION_ID


def connect(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    if conn.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
        conn.close()
        raise ValueError("This is not a research database")
    return conn


def unpack(conn, value):
    if isinstance(value, dict) and set(value) == {"$literal"}:
        return {k: unpack(conn, v) for k, v in value["$literal"].items()}
    if isinstance(value, dict) and "$blob" in value and "size" in value:
        row = conn.execute("SELECT data FROM blobs WHERE blob_key=?", (value["$blob"],)).fetchone()
        if row is None:
            raise ValueError("Missing research payload blob")
        data = bytes(row[0])
        if value.get("encoding") == "utf-8":
            return data.decode()
        return {"encoding": "base64", "data": base64.b64encode(data).decode()}
    if isinstance(value, dict):
        return {k: unpack(conn, v) for k, v in value.items()}
    if isinstance(value, list):
        return [unpack(conn, v) for v in value]
    return value


def failure_journal(path, run_id):
    journal = Path(str(Path(path).resolve()) + ".errors.jsonl")
    if not journal.exists():
        return []
    found = []
    for line in journal.read_text().splitlines():
        try:
            item = json.loads(line)
            if item.get("run_id") == run_id:
                found.append(item)
        except ValueError:
            found.append({"error": "Unreadable capture failure journal entry"})
    return found


def summary(conn, run_id, path):
    row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown research run {run_id}")
    result = dict(row)
    result["metadata"] = unpack(conn, json.loads(result["metadata"]))
    calls = [dict(r) for r in conn.execute(
        "SELECT c.call_id,c.agent_id,c.purpose,c.status,c.duration_ms,u.cost_usd,u.cost_nu,"
        "u.cost_complete,u.uncached_input,u.cache_read,u.output FROM model_calls c "
        "LEFT JOIN usage_costs u USING(run_id,call_id) WHERE c.run_id=? ORDER BY c.started_at", (run_id,))]
    result["calls"] = calls
    result["known_cost_usd"] = sum(c["cost_usd"] or 0 for c in calls)
    result["unpriced_calls"] = sum(not c["cost_complete"] for c in calls if c["status"] != "refused")
    result["cost_complete"] = result["unpriced_calls"] == 0
    result["issues"] = [dict(r) for r in conn.execute("SELECT * FROM capture_issues WHERE run_id=?", (run_id,))]
    result["capture_failures"] = failure_journal(path, run_id)
    if (result["capture_failures"] or result["issues"] or not row["ended_at"]
            or any(c["status"] == "open" for c in calls)):
        result["capture_status"] = "partial"
    if result["capture_failures"] or not row["ended_at"]:
        result["cost_complete"] = False
    return result


def export(conn, run_id):
    for row in conn.execute("SELECT * FROM events WHERE run_id=? ORDER BY sequence", (run_id,)):
        item = dict(row)
        item["payload"] = unpack(conn, json.loads(item["payload"]))
        yield json.dumps(item, ensure_ascii=False) + "\n"


def transcript(conn, run_id):
    for row in conn.execute("SELECT call_id,status FROM model_calls WHERE run_id=? ORDER BY started_at", (run_id,)):
        yield f"\n=== Call {row['call_id']} ({row['status']}) ===\n"
        for message in conn.execute("SELECT * FROM call_messages WHERE run_id=? AND call_id=? "
                                    "AND view IN ('sent','response') ORDER BY "
                                    "CASE view WHEN 'sent' THEN 0 ELSE 1 END,choice_index,position",
                                    (run_id, row["call_id"])):
            payload = unpack(conn, json.loads(message["payload"]))
            yield f"[{message['view']} / {message['role']}] {json.dumps(payload, ensure_ascii=False)}\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("command", choices=("list", "summary", "export", "transcript"))
    parser.add_argument("--run", help="research run UUID (not the operational run ID)")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.command != "list" and not args.run:
        parser.error("--run is required")
    if args.output and (Path(args.output).resolve() == Path(args.db).resolve()
                       or (Path(args.output).exists() and Path(args.output).samefile(args.db))):
        parser.error("output must not overwrite the database")
    conn = connect(args.db)
    try:
        if args.command == "list":
            lines = (json.dumps(dict(r)) + "\n" for r in conn.execute(
                "SELECT run_id,runtime_run_id,started_at,status,capture_status FROM runs ORDER BY started_at"))
        else:
            report = summary(conn, args.run, args.db)  # validates the run and capture completeness
            if args.command == "summary":
                lines = [json.dumps(report, indent=2, ensure_ascii=False) + "\n"]
            elif args.command == "export":
                lines = export(conn, args.run)
            else:
                lines = transcript(conn, args.run)
        if args.output:
            with open(args.output, "w") as f:
                f.writelines(lines)
        else:
            for line in lines:
                print(line, end="")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
