"""SWE-bench Verified instances: id, official image, problem statement, base commit.

Why it exists: the host needs the task and the environment; nothing else. The
official image already contains the repository at base_commit, so no clone is made.
What it must never do: expose the gold patch or the hidden tests to the agent.
"""

from dataclasses import dataclass

# Fixed before any run, by rule.
#   dev   the development instance, kept out of every comparison
#   mid5  SWE-bench Verified difficulty "15 min - 1 hour", light repositories (pylint,
#         pytest, sphinx; images ~1 GB), gold patch touching 2+ files (more to explore).
#         The first matches in instance-id order, at most two per repository.
#   quick3 SWE-bench Verified "<15 min fix" instances already used before (check5), for
#         runs of a few minutes each: the whole observe -> replay -> autopilot loop in ~20 min.
SETS = {
    "dev": ["pytest-dev__pytest-5809"],
    "mid5": ["pylint-dev__pylint-6386", "pytest-dev__pytest-5840", "pytest-dev__pytest-8399",
             "sphinx-doc__sphinx-10673", "sphinx-doc__sphinx-8593"],
    "quick3": ["pytest-dev__pytest-7432", "psf__requests-2317", "pallets__flask-5014"],
}


@dataclass
class Instance:
    instance_id: str
    repo: str
    base_commit: str
    image: str
    problem_statement: str


def load(instance_ids: list[str], dataset: str = "SWE-bench/SWE-bench_Verified") -> list[Instance]:
    from datasets import load_dataset  # installed with swebench

    rows = {r["instance_id"]: r for r in load_dataset(dataset, split="test")
            if r["instance_id"] in set(instance_ids)}
    missing = [i for i in instance_ids if i not in rows]
    if missing:
        raise ValueError(f"not in {dataset}: {missing}")
    return [Instance(i, rows[i]["repo"], rows[i]["base_commit"], rows[i]["image"],
                     rows[i]["problem_statement"]) for i in instance_ids]
