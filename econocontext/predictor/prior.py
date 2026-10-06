"""Fixed guesses: the predictor with no knowledge of the session (the baseline for Jev)."""

NEEDED_AGAIN = {"Read": 0.5, "Agent": 0.5, "Task": 0.5}  # anything else: DEFAULT
DEFAULT = 0.3


class Prior:
    name = "prior"

    def arrival(self, task, convo, item):
        return {"needed_again": NEEDED_AGAIN.get(item["tool"], DEFAULT), "lifetime": "few_calls",
                "relevant": None}, None

    def live(self, task, convo, items):
        # Without knowledge of the trajectory, nothing is ever judged done: no compaction.
        return {"done": {i["id"]: 0.0 for i in items}, "phase": None, "portion_done": 0.0}, None

    def subtask(self, task, convo, prompt, items):
        return {"needed": {i["id"]: DEFAULT for i in items}}, None
