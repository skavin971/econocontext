"""EconoViewAgent: EconoCLM-View. CLM's ClmAgent with VIEW.md as the context.

CLM is imported, never copied or patched. On top of EconoCLM-Tools' agent (lossless
store of every output as obs N, `econo get` / `econo search`, stale-file tracking, the
[econo] line) this agent:

  - replaces CLM's ContextEnv with ViewContextEnv (env.py): the model edits VIEW.md,
    whose lines are its next prompt, in its own order;
  - replaces only the "Managing your context" section of CLM's system prompt with
    TEXT-VIEW (system_section.md), per instance: it wraps this agent's own
    `_resume.replay`, which CLM's run calls once on the opening messages, before the
    first snapshot and budget check. CLM's module and other agents are not touched;
  - reports the [econo] line by view position (quote/view_status.py);
  - adds no SKILL.md text (its skill dir carries only the `econo` tool).

Extra kwargs: econo_run_dir (as EconoCLM-Tools), econo_mode ("gemini" or "qwen").
"""

from pathlib import Path
from typing import Any

from clm_harness.clm_agent import harness as clm

from ...quote.costmode import mode as cost_mode
from ...quote.view_status import view_status_line
from ..econo_clm_v11.agent import EconoClmV11Agent
from .env import ViewContextEnv

SECTION = "## Managing your context"
TEXT_VIEW = (Path(__file__).parent / "system_section.md").read_text().strip()
COMPACTION_HINT = (f"Remove lines you no longer need from /tmp/.live_ctx/VIEW.md (turn K, obs N, "
                   "obs N [lines A-B], note NAME: TEXT); anything you remove stays retrievable as obs N.")


def swap_section(system: str, budget_str: str) -> str:
    """CLM's system prompt with only its "Managing your context" section replaced."""
    tpl = clm._SYSTEM_TEMPLATE
    a, b = tpl.index(SECTION), tpl.index("{{finish_instructions}}")
    old = tpl[a:b].replace("{{context_budget}}", budget_str)
    if system.count(old) != 1:
        raise RuntimeError("EconoCLM-View: CLM's 'Managing your context' section not found exactly once")
    return system.replace(old, TEXT_VIEW + "\n\n")


class EconoViewAgent(EconoClmV11Agent):

    def __init__(self, *args: Any, econo_mode: str = "gemini", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        old = self._ctx
        self._ctx = ViewContextEnv(
            paths=old.paths, budget=self._budget, protect=self._protect,
            persistent_bash=self.persistent_bash, command_timeout=self.command_timeout,
            observation_max_chars=self.observation_max_chars, obs_cfg=old.obs_cfg,
            allow_edit_growth=self.allow_edit_growth,
            context_budget_tokens=self.context_budget_tokens or 0,
            obs_text=self._econo.obs_text, run_dir=self._econo.run_dir)
        old.host_mirror.unlink(missing_ok=True)
        self._host_mirror = self._ctx.host_mirror
        self._budget.compaction_hint = COMPACTION_HINT
        self._econo.econo_mode = econo_mode
        self._econo.cost = cost_mode(econo_mode)
        self._econo.build_status = self._view_status
        orig_replay = self._resume.replay
        budget_str = (f"{self._budget.strict_target or self.context_budget_tokens} tokens"
                      if self.context_budget_tokens else "your model's full context window")

        def replay(messages, **kw):
            messages[0]["content"] = swap_section(messages[0]["content"], budget_str)
            return orig_replay(messages, **kw)

        self._resume.replay = replay

    def _view_status(self, shown: list[dict], c: int | None, stale: list[str]) -> Any:
        h = self._econo
        line = view_status_line(
            shown, self._ctx.line_starts, cached=c, uncached=h.last.uncached if h.last else None,
            prompt_tokens=h.last.prompt if h.last else None, hits=h.hits(), read=h.last_read,
            run_cost=h.run_cost(), n_stored=h.n_obs, stale=stale, protect=self._ctx.protect,
            k=h.k, thinking=h.thinking if h.cost.name == "gemini" else None,
            mode_name=h.meter.mode if h.cost.name == "gemini" else "none", cost=h.cost,
            count=h.count)

        class Status:  # the hooks only read .line
            pass
        st = Status()
        st.line = line
        return st

    async def run(self, instruction: str, environment: Any, context: Any) -> None:
        try:
            await super().run(instruction, environment, context)
        finally:
            try:
                self._ctx.save_state()
            except Exception as exc:
                self._econo.error("view_save", exc)
