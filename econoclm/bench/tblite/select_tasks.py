"""Pick the 10 TBLite tasks for the experiment: a fixed seed over the sorted task list.

  python -m econoclm.bench.tblite.select_tasks --tblite ../OpenThoughts-TBLite

A task is a folder with task.toml. Writes tasks.txt (one task name per line) next
to this file.
"""

import argparse
import random
from pathlib import Path

SEED = 20261003
N_TASKS = 10
HERE = Path(__file__).parent


def all_tasks(tblite: Path) -> list[str]:
    return sorted(p.parent.name for p in tblite.glob("*/task.toml"))


def select(tasks: list[str], n: int = N_TASKS, seed: int = SEED) -> list[str]:
    return random.Random(seed).sample(sorted(tasks), n)


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
