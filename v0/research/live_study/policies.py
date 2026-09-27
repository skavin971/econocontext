"""The experiment's only variable: how a run manages its context.

Each policy is a harness method, Config overrides and Limits overrides. The
threshold (60K estimated tokens, half the shared 120K window) and the floor of
three newest tool outputs are Config defaults, identical for every policy and
every model, and fixed before any run.
"""

POLICIES = {
    # Never evicts. The naive baseline.
    "P0": ("react", {"context_policy": "append-only"}, {}),
    # Over the threshold, a billed model call summarizes the middle of the history.
    "P1": ("react", {"context_policy": "compact"}, {}),
    # Over the threshold, every tool output but the newest three is cleared.
    "P2": ("react", {"context_policy": "clear-oldest"}, {}),
    # EconoContext: place each large result by its forecast holding cost, and
    # clear old ones only when the cache economics say so.
    "P3": (
        "econocontext",
        # 60 calls: the median length of all 67,074 public SWE-rebench OpenHands
        # trajectories, (123 messages - 2) / 2. Dataset-wide, not per task.
        {"context_policy": "cache-aware", "lifetime_prior": 60},
        # Placement is decided on every large result, not only under pressure.
        {"plan_pressure": 0.02},
    ),
}
