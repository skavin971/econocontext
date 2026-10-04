"""The two arms' Harbor commands are identical except the two allowed differences
(agent class, skill_dirs) plus econo_run_dir (where EconoCLM writes its logs)."""

import subprocess
import sys
from pathlib import Path

import yaml

from econoclm.bench.tblite.run import ARMS, build_command, jobs

ALLOWED = {"agent", "skill_dirs", "econo_run_dir"}


def parsed(argv):
    """argv -> {'agent': ..., kwarg name: value, ...} for comparison."""
    out, i = {}, 0
    while i < len(argv):
        if argv[i] == "-a":
            out["agent"] = argv[i + 1]
            i += 2
        elif argv[i] == "--agent-kwarg":
            k, v = argv[i + 1].split("=", 1)
            out[k] = v
            i += 2
        elif argv[i].startswith("-"):
            out[argv[i]] = argv[i + 1]
            i += 2
        else:
            out.setdefault("_positional", []).append(argv[i])
            i += 1
    return out


def test_commands_differ_only_where_allowed(tmp_path):
    kw = dict(tblite=Path("/t"), out_dir=tmp_path, port=8787, harbor="harbor")
    raw_id, raw = build_command("raw", "task-a", 1, **kw)
    eco_id, eco = build_command("econo", "task-a", 1, **kw)
    assert (raw_id, eco_id) == ("raw-task-a-r1", "econo-task-a-r1")
    r = parsed([a.replace(raw_id, "RUN") for a in raw])
    e = parsed([a.replace(eco_id, "RUN") for a in eco])
    differ = {k for k in set(r) | set(e) if r.get(k) != e.get(k)}
    assert differ == ALLOWED
    assert r["agent"] == "clm_harness.clm_agent.harness:ClmAgent"
    assert e["agent"] == "econoclm.arms.econo_clm.agent:EconoClmAgent"
    assert Path(e["skill_dirs"]).is_absolute() and (Path(e["skill_dirs"]) / "SKILL.md").exists()
    # The settings the prompt fixes, in both arms.
    for arm in (r, e):
        assert arm["context_budget_tokens"] == "32000" and arm["max_tokens"] == "8192"
        assert arm["max_steps"] == "64" and arm["command_timeout"] == "180"
        assert arm["temperature"] == "0.7" and arm["top_p"] == "0.95"
        assert arm["send_chat_template_kwargs"] == "false" and arm["cost_metric"] == "usd"
        assert arm["api_base"] == "http://127.0.0.1:8787/run/RUN/v1"
        assert arm["--agent-timeout-multiplier"] == "4"
        assert arm["-e"] == "docker"


def test_configs_differ_only_in_agent_and_skill_dirs():
    raw, eco = (yaml.safe_load(ARMS[a].read_text()) for a in ("raw", "econo"))
    assert raw["model"] == eco["model"]
    rk, ek = dict(raw["agent_kwargs"]), dict(eco["agent_kwargs"])
    assert ek.pop("skill_dirs") and rk == ek


def test_jobs_alternate_arms():
    assert jobs(["raw", "econo"], ["a", "b"], 1) == [
        ("raw", "a", 1), ("econo", "a", 1), ("raw", "b", 1), ("econo", "b", 1)]


def test_dry_run_cli_prints_commands():
    out = subprocess.run([sys.executable, "-m", "econoclm.bench.tblite.run", "--dry-run",
                          "--limit", "2"], capture_output=True, text=True, check=True).stdout
    lines = out.strip().splitlines()
    assert len(lines) == 4 and all(" trial start " in ln for ln in lines)
    assert "OPENAI_API_KEY" not in out and "AGENT_PLATFORM_API_KEY" not in out
