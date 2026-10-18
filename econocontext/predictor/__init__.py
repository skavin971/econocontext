"""Predictors: answer the rules' questions about context. Jev now; our own classifier later.

Every predictor has the same two methods, each returning (answers, usage):
  arrival(task, convo, item)         -> needed_again (0-1), lifetime (label), relevant ({chunk: prob} or None)
  segment(task, turns, items, boundaries) -> done ({item id: 0-1}), segment_done (0-1), segment_end (turn)
Predictors only select; they never write text. Prices and decisions live elsewhere, so a new
predictor changes no economics.
"""

LIFETIMES = {
    "this_call": "Only for the very next step; after that the agent will not look at it again",
    "few_calls": "For the next few steps of the current sub-task",
    "this_part": "Until the current part of the task (e.g. investigating, fixing, testing) is done",
    "whole_task": "Until the whole task is finished",
}


def chunks(text: str, lines_per_chunk: int) -> list[tuple[int, int]]:
    """1-based (first, last) line numbers of each chunk, at most 255 chunks."""
    n = max(1, len(text.splitlines()))
    size = max(lines_per_chunk, -(-n // 255))
    return [(start, min(n, start + size - 1)) for start in range(1, n + 1, size)]
