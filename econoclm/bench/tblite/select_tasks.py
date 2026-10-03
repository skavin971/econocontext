"""Pick the 10 TBLite tasks for the experiment: a fixed seed over the sorted task list.

  python -m econoclm.bench.tblite.select_tasks --tblite ../OpenThoughts-TBLite

A task is a folder with task.toml. Writes tasks.txt (one task name per line) next
to this file. bench/tblite/health_check.py may later swap a task whose reference
solution fails for the next task in the same seeded order.
"""

import argparse
import random
from pathlib import Path

SEED = 20261003
N_TASKS = 10
HERE = Path(__file__).parent


def all_tasks(tblite: Path) -> list[str]:
    return sorted(p.parent.name for p in tblite.glob("*/task.toml"))


def seeded_order(tasks: list[str], seed: int = SEED) -> list[str]:
    """Every task in the fixed seeded order. Its first N_TASKS are the experiment's
    tasks; the ones after are the replacements, in order (health_check.py)."""
    return random.Random(seed).sample(sorted(tasks), len(tasks))


def select(tasks: list[str], n: int = N_TASKS, seed: int = SEED) -> list[str]:
    # Same as random.Random(seed).sample(sorted(tasks), n): a full-length sample
    # starts with the same n tasks (checked in tests/test_health_check.py).
    return seeded_order(tasks, seed)[:n]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tblite", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=HERE / "tasks.txt")
    args = ap.parse_args()
    tasks = all_tasks(args.tblite)
    chosen = select(tasks)
    args.out.write_text("\n".join(chosen) + "\n")
    print(f"{len(tasks)} tasks found; chose {len(chosen)} -> {args.out}")
    print("\n".join(chosen))


if __name__ == "__main__":
    main()
