"""EconoContext: a harness-agnostic, cost-based optimizer for LLM agent systems.

It plugs into an existing agent harness at a few intercept points and decides how
each step is physically carried out: what each window contains and in what order,
what is retrieved from the store instead of recomputed, and which worker runs
delegated work. The harness keeps its loop, tools, state and security; if
EconoContext fails, the harness proceeds exactly as it would without it.

This package is the core. It imports only the standard library and PyYAML; host
frameworks live under agents/, and model access goes through gateway/.
"""

__version__ = "0.3.0.dev0"
