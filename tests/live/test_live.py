"""Live tests: real Gemini on Vertex AI and a real SWE-bench container. They cost money.

Run explicitly:  set -a; . ./.env; set +a; .venv/bin/python -m pytest -m live tests/live
Skipped without credentials or Docker.
"""

import os
import time

import pytest

from hosts.swebench_deepagents.evaluate import docker_available

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(not os.environ.get("AGENT_PLATFORM_API_KEY"),
                                 reason="no Vertex credentials in the environment"),
              pytest.mark.skipif(not docker_available(), reason="Docker is not running")]


def _run(arm, mode):
    from econocontext import config as config_module
    from hosts.swebench_deepagents import run, tasks
    cfg = config_module.load(run.CONFIG_DIR).raw
    inst = tasks.load(cfg["instances"]["dev"], cfg["instances"]["dataset"])[0]
    label = f"livetest-{int(time.time())}"
    db_path = str(run.ROOT / cfg["storage"]["db_path"])
    run_id, patch = run.run_instance(inst, arm, mode, label, cfg, db_path)
    return run_id, patch, db_path, inst, label


def test_observe_mode_end_to_end():
    from econocontext.store.db import AgentDB
    run_id, _, db_path, _, _ = _run("econo", "observe")
    db = AgentDB(db_path)
    outcomes = db.rows("SELECT * FROM outcomes WHERE run_id=?", (run_id,))
    assert outcomes, "no model calls were recorded"
    assert all(o["output"] is not None and o["uncached_input"] is not None for o in outcomes)
    agents = db.rows("SELECT agent_id, parent_id FROM agents WHERE run_id=?", (run_id,))
    assert any(a["agent_id"] == f"{run_id}:root" for a in agents)
    assert all(d["applied"] == 0 for d in db.rows("SELECT applied FROM decisions WHERE run_id=?",
                                                   (run_id,)))


def test_autopilot_exact_operators_produces_an_evaluated_patch(tmp_path):
    import json

    from hosts.swebench_deepagents.evaluate import evaluate
    run_id, patch, _, inst, label = _run("econo", "autopilot")
    preds = tmp_path / "preds.jsonl"
    preds.write_text(json.dumps({"instance_id": inst.instance_id, "model_name_or_path": "econo",
                                 "model_patch": patch}) + "\n")
    result = evaluate(preds, [inst.instance_id], f"{label}-eval", workdir=tmp_path)
    assert inst.instance_id in result["per_instance"]  # evaluated, pass or fail
