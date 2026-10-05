"""Read-only reproducibility snapshots. Never records environment variables or keys."""

import importlib.metadata
import platform
import subprocess
from dataclasses import asdict

from econocontext import config


def git(root, *args):
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                                text=True, timeout=10)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def snapshot(home, args, instance, overrides, spec):
    versions = {}
    for name in ("econocontext", "omnigent", "openai-agents", "swebench", "PyYAML"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    engine_overrides = dict(overrides)
    learned = bool(engine_overrides.pop("learned", False))
    resolved = config.load(home / "config", engine_overrides)
    return dict(label=args.label, arm=args.arm, mode=args.mode, jev=args.jev,
                learned=learned, overrides=overrides,
                dataset="SWE-bench/SWE-bench_Verified", dataset_split="test",
                task=asdict(instance), prompt=instance.problem_statement, agent_spec=spec,
                config=resolved.raw, config_fingerprint=resolved.fingerprint,
                pricing=asdict(resolved.card), python=platform.python_version(),
                packages=versions, code_revision=git(home, "rev-parse", "HEAD"),
                code_status=git(home, "status", "--porcelain"),
                code_diff=git(home, "diff", "HEAD"), max_minutes=args.max_minutes,
                coverage={"worker_ids": "first-message hash; may collide for identical tasks",
                          "tool_attribution": "native only when policy events provide IDs",
                          "capture": "gateway-visible payloads and delivered policy events"})
