"""One-time setup: an Omnigent provider for sub-agents, and Gemini CLI as an ACP agent.

Why it exists: Omnigent 0.15.0 drops `use_responses: false` for inline sub-agents, so a
worker would call the Responses API, which Vertex does not serve. A provider with
`wire_api: chat` does reach sub-agents (omnigent/runtime/workflow.py), and a worker
spec selects it with `auth: {type: provider, name: econo}`. The provider's URL is
global, so it points at the gateway's /current route: the run benchmarks/swebench/run.py marked current.

Omnigent's host daemon launches the generic `acp` harness only when at least one ACP
agent is registered in the user config, even though harness/specs/gemini/agent.yaml embeds its
own (which is what a run actually uses). So Gemini CLI is registered here too.

It edits ~/.omnigent/config.yaml (Omnigent's user config), after a backup next to it.
The key is a placeholder: the gateway adds the real one.

Run once: .venv/bin/python harness/setup.py [--gateway http://127.0.0.1:8787]
"""

import argparse
import shutil
from pathlib import Path

import yaml

CONFIG = Path.home() / ".omnigent" / "config.yaml"
GEMINI = Path(__file__).resolve().parents[1] / "data" / "tools" / "node_modules" / ".bin" / "gemini"


def provider(gateway: str) -> dict:
    return {"kind": "gateway",
            "openai": {"base_url": f"{gateway}/current/agent/worker/v1",
                       "api_key": "econo-placeholder", "wire_api": "chat",
                       "models": {"default": "google/gemini-3.6-flash"}}}


def gemini_agent() -> dict:
    return {"name": "Gemini CLI", "command": f"{GEMINI} --acp", "omnigent_mcp": False,
            "inject_system_prompt": False}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--gateway", default="http://127.0.0.1:8787")
    gateway = p.parse_args().gateway.rstrip("/")
    config = yaml.safe_load(CONFIG.read_text()) if CONFIG.exists() else {}
    if CONFIG.exists():
        shutil.copy(CONFIG, CONFIG.with_suffix(".yaml.bak"))
    config = config or {}
    config.setdefault("providers", {})["econo"] = provider(gateway)
    acp = config.setdefault("acp", {})
    agents = [a for a in acp.get("agents") or [] if a.get("name") != "Gemini CLI"]
    acp["agents"] = agents + [gemini_agent()]
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(yaml.safe_dump(config, sort_keys=False))
    print(f"provider 'econo' -> {gateway}/current/agent/worker/v1 and ACP agent "
          f"'Gemini CLI' written to {CONFIG}")


if __name__ == "__main__":
    main()
