"""EconoContext for LangChain's Deep Agents (deepagents 0.7.19).

Two installs, so an experiment's two arms differ only in the decision layer:
- install_measurement(engine, ...)  -> callbacks for agent.invoke(config=...). Both arms.
- install_decisions(engine, host)   -> a middleware factory: one instance for the root
                                       and one for every subagent spec. EconoContext arm only.
"""

from econocontext.engine import EconoContext

from ..providers import anthropic_usage, gemini_usage, openai_usage
from .callbacks import BudgetExceeded, MeasurementCallback
from .executors import DeepAgentsHost
from .middleware import EconoMiddleware

USAGE_MAPPERS = {"vertex_gemini": gemini_usage.to_provider_usage,
                 "anthropic": anthropic_usage.to_provider_usage,
                 "openai": openai_usage.to_provider_usage}

__all__ = ["BudgetExceeded", "DeepAgentsHost", "install_decisions", "install_measurement",
           "root_agent_id"]


def root_agent_id(engine: EconoContext) -> str:
    return f"{engine.run_id}:root"


def install_measurement(engine: EconoContext, budget_usd=None, spent_elsewhere_usd=0.0,
                        global_budget_usd=None) -> list:
    provider = engine.cfg["model"]["provider"]
    engine.registry.ensure_agent(root_agent_id(engine),
                                 window_max_tokens=engine.cfg["limits"]["window_max_tokens"])
    return [MeasurementCallback(engine, root_agent_id(engine), USAGE_MAPPERS[provider], budget_usd,
                                spent_elsewhere_usd, global_budget_usd)]


def install_decisions(engine: EconoContext, host: DeepAgentsHost):
    """Returns a zero-argument factory; call it once per agent (root and each subagent)."""
    def make():
        return EconoMiddleware(engine, host, root_agent_id(engine))
    return make
