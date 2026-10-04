"""EconoClmV11Agent: the v1 EconoCLM agent with cut tags that name the exact missing lines.

Used by both tool-engagement arms (REPORT.md deviation 8):
  v1.1 "facts only"  arms/econo_clm_v11/  (its SKILL.md: the EconoCLM section)
  v1.2 "guided"      arms/econo_clm_v12/  (the same section plus three usage sentences)
The two arms differ only in their SKILL.md. Their `econo` tool adds `note` / `notes`.

One change from v1: a cut output's tag reads "[obs 3] lines 120-310 not shown: econo get
3 120-310" (EconoHooks.cut_tag with cut_lines, computed from CLM's head/tail cut).
Status line, edit quote and stale flags are v1's.
"""

from typing import Any

from ..econo_clm.agent import EconoClmAgent


class EconoClmV11Agent(EconoClmAgent):

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._econo.cut_lines = True
        self._econo.obs_cfg = dict(getattr(self._ctx, "obs_cfg", None) or {})
