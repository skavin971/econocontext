"""SWE-bench Verified instances: id, official image, problem statement, base commit.

Why it exists: the host needs the task and the environment; nothing else. The
official image already contains the repository at base_commit, so no clone is made.
What it must never do: expose the gold patch or the hidden tests to the agent.
"""

from dataclasses import dataclass


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
