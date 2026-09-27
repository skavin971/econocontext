"""Choose SWE-rebench tasks for the live study by a stated rule, and write fixtures.

Inputs are the public parquet files, downloaded by hand (see README.md):
  --trajectories  nebius/SWE-rebench-openhands-trajectories  (only to choose tasks)
  --tasks         nebius/SWE-rebench, test split (one or more files)

The rule, fixed before any run:
  1. every OpenHands reference run of the task took >= 50 assistant turns
     (long enough to reach the regime the cost model is about),
  2. at least 3 reference runs, and all of them resolved (known to be solvable),
  3. pytest log parser, <= 20 FAIL_TO_PASS and 5..300 PASS_TO_PASS tests,
  4. a published image; then ordered by compressed image size, smallest first.

The trajectories are read for their length and outcome only. Nothing from them
is shown to an agent.
"""

import argparse
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

FIXTURES = Path(__file__).resolve().parents[2] / "src" / "agents" / "swe" / "fixtures"


def image_bytes(image):
    # Docker Hub rate-limits anonymous lookups; back off rather than drop the task.
    url = f"https://hub.docker.com/v2/repositories/{image}/tags/latest"
    for attempt in range(6):
        try:
            return json.load(urllib.request.urlopen(url, timeout=30)).get("full_size")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
        except OSError:
            pass
        time.sleep(2**attempt)
    return None


def candidates(trajectories, tasks):
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    table = pq.read_table(trajectories, columns=["instance_id", "resolved", "trajectory"])
    runs = pd.DataFrame(
        dict(
            instance_id=table["instance_id"].to_pylist(),
            resolved=table["resolved"].to_pylist(),
            # system + first user message, then one assistant and one tool per turn
            turns=[(n - 2) // 2 for n in pc.list_value_length(table["trajectory"]).to_pylist()],
        )
    )
    per = runs.groupby("instance_id").agg(
        runs=("turns", "size"),
        turns_min=("turns", "min"),
        turns_median=("turns", "median"),
        solve_rate=("resolved", "mean"),
    )
    meta = pd.concat([pd.read_parquet(p) for p in tasks]).set_index("instance_id")
    meta["parser"] = meta.install_config.map(lambda c: (c or {}).get("log_parser"))
    joined = per.join(meta, how="inner")
    keep = joined[
        (joined.turns_min >= 50)
        & (joined.runs >= 3)
        & (joined.solve_rate == 1.0)
        & (joined.parser == "parse_log_pytest")
        & (joined.FAIL_TO_PASS.map(len) <= 20)
        & joined.PASS_TO_PASS.map(len).between(5, 300)
        & joined.docker_image.notna()
    ].copy()
    with ThreadPoolExecutor(4) as pool:
        keep["image_bytes"] = list(pool.map(image_bytes, keep.docker_image))
    return keep[keep.image_bytes.notna()].sort_values("image_bytes")


def write_fixture(instance_id, row):
    root = FIXTURES / instance_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "GOAL.md").write_text(row.problem_statement.strip() + "\n")
    task = dict(
        instance_id=instance_id,
        repo=row.repo,
        image=row.docker_image,
        base_commit=row.base_commit,
        test_patch=row.test_patch,
        FAIL_TO_PASS=list(row.FAIL_TO_PASS),
        PASS_TO_PASS=list(row.PASS_TO_PASS),
        reference=dict(
            runs=int(row.runs),
            turns_min=int(row.turns_min),
            turns_median=float(row.turns_median),
            solve_rate=float(row.solve_rate),
            image_bytes=int(row.image_bytes),
        ),
    )
    (root / "task.json").write_text(json.dumps(task, indent=2) + "\n")
    # The upstream fix, used only to check the verifier itself. Never staged.
    (root / "reference.diff").write_text(row.patch)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trajectories", required=True)
    parser.add_argument("--tasks", nargs="+", required=True)
    parser.add_argument("--count", type=int, default=2)
    parser.add_argument("--skip", type=int, default=0)
    args = parser.parse_args()
    chosen = candidates(args.trajectories, args.tasks)
    print(f"{len(chosen)} tasks meet the rule")
    for instance_id, row in chosen.iloc[args.skip : args.skip + args.count].iterrows():
        write_fixture(instance_id, row)
        print(f"  {instance_id}  turns>={row.turns_min}  image={row.image_bytes / 1e9:.2f}GB")


if __name__ == "__main__":
    main()
