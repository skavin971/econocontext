"""run.py with the tool-engagement arms registered (REPORT.md deviation 8).

  python -m econoclm.bench.tblite.run_arms --arms econo11 --reps 1 --workers 2 --date <phase>

run.py is a frozen shared part (tag econoclm-shared-v1) and knows only `raw` and `econo`.
This entry point adds the arms to its ARMS table at runtime and gives them an
econo_run_dir like `econo`'s, then runs run.main() unchanged: same queueing, spend
check, gateway check, tokenizer and environment.

  econo11  v1.1 "facts only"  arms/econo_clm_v11/config.yaml
  econo12  v1.2 "guided"      arms/econo_clm_v12/config.yaml

Arm names have no hyphen: the analysis splits run ids at the first "-".
"""

from pathlib import Path

from . import run

EXTRA_ARMS = {"econo11": run.ECONOCLM / "arms/econo_clm_v11/config.yaml",
              "econo12": run.ECONOCLM / "arms/econo_clm_v12/config.yaml"}

_build_command = run.build_command


def build_command(arm: str, task: str, rep: int, *, out_dir: Path, **kw):
    run_id, argv = _build_command(arm, task, rep, out_dir=out_dir, **kw)
    if arm in EXTRA_ARMS:
        i = argv.index("--agent-timeout-multiplier")
        argv[i:i] = ["--agent-kwarg", f"econo_run_dir={out_dir / run_id}"]
    return run_id, argv


def install() -> None:
    run.ARMS.update(EXTRA_ARMS)
    run.build_command = build_command


if __name__ == "__main__":
    install()
    run.main()
