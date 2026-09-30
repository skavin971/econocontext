"""Claude Code's sub-agents: tracked at the gateway from the requests, and placed by the
policy (RESUME = deny the new Agent call with a reason naming the idle worker)."""

import json

import omnigent_layer
from econocontext.evidence import EvidenceEvent, make_ref
from econocontext.store.db import AgentDB
from omnigent_layer import claude_workers as cw, register_run
from omnigent_layer.policy import econocontext

CLAUDE = {"model": {"provider": "anthropic", "name": "claude-sonnet-5"}, "allowlist": {"ZONED": False}}
RESUME = {**CLAUDE, "allowlist": {"ZONED": False, "RESUME": True},
          "constraints": {"max_quality_risk": 0.2}}
PROMPT = "Find where the autodoc member filter decides whether a variable is public."


def root_after_agent(agent_id="a1b2c3", prompt=PROMPT):
    return {"messages": [
        {"role": "user", "content": "fix the issue"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "u1", "name": "Agent",
                                           "input": {"description": "Find member filter",
                                                     "prompt": prompt,
                                                     "subagent_type": "general-purpose"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "u1", "content": [
            {"type": "text", "text": f"Async agent launched successfully.\nagentId: {agent_id} "
                                     f"(internal ID). Use SendMessage with to: '{agent_id}'"}]}]}]}


def worker_loop(db, run_id, context_key="ctx-w1", prompt=PROMPT):
    """The sub-agent's first request, one Read it did, and its handback."""
    cw.track_request(db, run_id, {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "<system-reminder>env</system-reminder>"},
        {"type": "text", "text": prompt}]}]}, context_key)
    db.execute("INSERT INTO runtime_spans (span_id, run_id, agent_id, kind, name, native_id, started_at, "
               "status, metadata) VALUES(?,?,?,?,?,?,?,?,?)",
               ("s1", run_id, f"{run_id}:root", "model", "claude-sonnet-5", "o1", "2026-09-30T00:00:00",
                "completed", json.dumps({"call_no": 1, "context_key": context_key})))
    db.execute("INSERT INTO outcomes (outcome_id, run_id, agent_id, phase, uncached_input, cache_read, "
               "cache_write, output, cost_usd, cost_complete, raw, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
               ("o1", run_id, f"{run_id}:root", "agent", 2, 20000, 3000, 100, 0.01, 1, "{}", "2026-09-30"))
    ref = make_ref("file", "sphinx/ext/autodoc/__init__.py", "sha256:1", "x " * 400)
    db.add_evidence_events(run_id, f"{run_id}:root", 1, [EvidenceEvent("acquired", "Read", "k",
                                                                       ref.source_key, ref)])
    cw.track_reply(db, run_id, context_key, [{"name": "SubagentHandback"}])


def test_a_worker_is_registered_tied_to_its_loop_and_idle_after_handback(tmp_path):
    register_run("w1", "econo", "observe", host="omnigent:claude-code", overrides=CLAUDE)
    db = AgentDB(omnigent_layer.DB_PATH)
    cw.track_request(db, "w1", root_after_agent(), "ctx-root")
    worker_loop(db, "w1")
    [w] = cw.workers(db, "w1", ttl_seconds=300)
    assert (w["worker_id"], w["type"], w["busy"], w["warm"]) == ("a1b2c3", "general-purpose", False, True)
    assert w["files"] == ["sphinx/ext/autodoc/__init__.py"] and w["resident_tokens"] == 23002
    # the root continues it: running again, counted once however often history repeats it
    cont = {"messages": [{"role": "user", "content": "fix the issue"},
                         {"role": "assistant", "content": [{"type": "tool_use", "id": "u2",
                                                            "name": "SendMessage",
                                                            "input": {"to": "a1b2c3", "message": "m"}}]},
                         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "u2",
                                                       "content": "Resuming agent a1b2c3"}]}]}
    cw.track_request(db, "w1", cont, "ctx-root")
    [row] = cw.report(db, "w1")
    assert (row["status"], row["resumed"]) == ("running", 1)


def agent_call(kind="general-purpose"):
    return {"type": "tool_call", "target": "Agent",
            "data": {"arguments": {"description": "Check the event filter", "subagent_type": kind,
                                   "prompt": "Now find where autodoc-skip-member is emitted."}}}


def test_autopilot_redirects_a_new_agent_to_the_idle_worker(tmp_path):
    register_run("w2", "econo", "autopilot", host="omnigent:claude-code", workdir=str(tmp_path),
                 overrides=RESUME)
    db = AgentDB(omnigent_layer.DB_PATH)
    cw.track_request(db, "w2", root_after_agent(), "ctx-root")
    worker_loop(db, "w2")
    verdict = econocontext("w2", str(tmp_path))(agent_call())
    assert verdict["result"] == "DENY"
    assert "SendMessage" in verdict["reason"] and "to: 'a1b2c3'" in verdict["reason"]
    assert "sphinx/ext/autodoc/__init__.py" in verdict["reason"]
    [d] = db.rows("SELECT chosen, applied FROM decisions WHERE run_id='w2' AND intercept='plan_dispatch'")
    assert (d["chosen"], d["applied"]) == ("RESUME", 1)


def test_observe_mode_only_logs_and_forks_are_left_alone(tmp_path):
    register_run("w3", "econo", "observe", host="omnigent:claude-code", workdir=str(tmp_path),
                 overrides=RESUME)
    db = AgentDB(omnigent_layer.DB_PATH)
    cw.track_request(db, "w3", root_after_agent(), "ctx-root")
    worker_loop(db, "w3")
    policy = econocontext("w3", str(tmp_path))
    assert policy(agent_call()) is None  # control: the new agent starts as Claude Code asked
    assert db.rows("SELECT chosen FROM decisions WHERE run_id='w3' AND intercept='plan_dispatch'")[0][0] \
        == "RESUME"  # ...but the decision EconoContext would have made is on record
    assert policy(agent_call(kind="fork")) is None


def test_a_busy_worker_is_never_chosen(tmp_path):
    register_run("w4", "econo", "autopilot", host="omnigent:claude-code", workdir=str(tmp_path),
                 overrides=RESUME)
    db = AgentDB(omnigent_layer.DB_PATH)
    cw.track_request(db, "w4", root_after_agent(), "ctx-root")
    worker_loop(db, "w4")
    cw.track_reply(db, "w4", "ctx-w1", [{"name": "Read"}])  # working again
    assert econocontext("w4", str(tmp_path))(agent_call()) is None
