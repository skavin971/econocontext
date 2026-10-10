import json
from datetime import date

from econocontext import config
from econocontext.pricing.ledger import Ledger
from econocontext.store.db import AgentDB
from econocontext.types import AgentNode, ProviderUsage
from harness.report import build_report, csv_text, render_text


def test_report_exports_costs_timings_and_no_raw_usage(tmp_path):
    cfg = config.load("config")
    db = AgentDB(tmp_path / "db.sqlite3")
    run_id = "label:econo:item:1"
    agent_id = f"{run_id}:root"
    db.start_run(run_id, "test", "item", "econo", "observe", cfg.card.model, 1.0,
                 cfg.fingerprint)
    db.upsert_agent(run_id, AgentNode(agent_id, None, None))
    Ledger(db, cfg.card).record(
        "call", run_id, agent_id, None, "agent",
        ProviderUsage(100, 900, None, 10, raw={"secret": "do not export"},
                      cache_write_applicable=False), date(2026, 9, 27))
    db.add_runtime_span("span", run_id, agent_id, "model", "chat", "call", None)
    db.finish_runtime_span("span", 12.5, "completed")

    report = build_report(db, cfg, "label")
    encoded = json.dumps(report)
    assert report["runs"][0]["totals"]["cost_usd"] == 0.00018
    assert report["runs"][0]["cost"]["actual_cost_usd"] == 0.00018
    assert report["runs"][0]["calls_by_phase"]["agent"]["calls"] == 1
    assert report["runs"][0]["spans"][0]["duration_ms"] == 12.5
    assert "secret" not in encoded
    assert "cost_usd" in csv_text(report).splitlines()[0]
    assert "a working, measured pipeline" in render_text(report)
