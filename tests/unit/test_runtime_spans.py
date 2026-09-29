from econocontext.engine import EconoContext
from econocontext.host import HostCapabilities


class Host:
    capabilities = HostCapabilities()
    pointer_store = None


def test_runtime_spans_track_parallel_activity_and_close_cleanly(tmp_path):
    eco = EconoContext("config", Host(), "r", host_name="test", arm="econo",
                       db_path=str(tmp_path / "db.sqlite3"), mode="observe")
    agent = "r:task:one"
    eco.registry.ensure_agent(agent)
    eco.start_span("s1", agent, "model", "chat", native_id="m1")
    eco.start_span("s2", agent, "tool", "read_file", native_id="t1")
    assert eco.db.rows("SELECT status FROM runtime_spans WHERE status='open'")
    assert eco.db.rows("SELECT status FROM agents WHERE agent_id=?", (agent,))[0]["status"] == "busy"

    eco.finish_span("s1", agent, 12.5)
    assert eco.db.rows("SELECT status FROM agents WHERE agent_id=?", (agent,))[0]["status"] == "busy"
    eco.finish_span("s2", agent, 4.0, "failed")
    assert eco.db.rows("SELECT status FROM agents WHERE agent_id=?", (agent,))[0]["status"] == "idle"
    rows = {r["span_id"]: dict(r) for r in eco.db.rows("SELECT * FROM runtime_spans")}
    assert rows["s1"]["status"] == "completed" and rows["s1"]["duration_ms"] == 12.5
    assert rows["s2"]["status"] == "failed" and rows["s2"]["ended_at"] is not None
