"""EconoClmAgent: CLM's own ClmAgent plus the EconoCLM hooks. Nothing else differs.

CLM is imported, never copied or patched. Three methods are extended:

  setup              CLM's setup, then /tmp/econo in the sandbox, then CLM's
                     ContextEnv.step is wrapped by EconoHooks.step (hooks.py)
  _query_with_retry  CLM's call, then the usage is kept (for the quote/status line)
  run                CLM's run; afterwards the sandbox's econo log is brought home

The model sees EconoCLM only through (a) the skill's SKILL.md, which CLM itself
appends to the system prompt (skill_dirs), and (b) the text the hooks add to each
tool result. CLM's root prompt is unchanged.

Extra kwarg: econo_run_dir (where econo.sqlite and the full outputs go; default
<logs_dir>/econo). The run id is read from api_base (.../run/<run_id>/v1); the
gateway's ledger is <econo_run_dir>/../gateway.sqlite unless ECONOCLM_GATEWAY_DB is set.
"""

import os
import re
from pathlib import Path
from typing import Any

from clm_harness.clm_agent.harness import ClmAgent

from .hooks import EconoHooks

RUN_ID = re.compile(r"/run/([^/]+)(?:/agent/[^/]+)?/v1/?$")


class EconoClmAgent(ClmAgent):

    def __init__(self, *args: Any, econo_run_dir: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        found = RUN_ID.search(self.api_base or "")
        run_id = found.group(1) if found else "unknown"
        run_dir = Path(econo_run_dir) if econo_run_dir else Path(self.logs_dir) / "econo"
        gateway_db = os.environ.get("ECONOCLM_GATEWAY_DB") or run_dir.parent / "gateway.sqlite"
        self._econo = EconoHooks(
            run_dir, run_id,
            observation_max_chars=self.observation_max_chars,
            protect=lambda: self._protect,
            state_dir=self._ctx.paths.state_dir,
            gateway_db=gateway_db,
            econo_path=f"{self._skills.mount_dir}/econo_db/econo",
        )

    async def setup(self, environment: Any) -> None:
        await super().setup(environment)
        try:
            await self._econo.setup(environment)
        except Exception as exc:
            self._econo.error("setup", exc)
        orig = self._ctx.step

        async def step(command, messages, *, environment, pending=None):
            return await self._econo.step(orig, command, messages,
                                          environment=environment, pending=pending)

        self._ctx.step = step

    async def _query_with_retry(self, model: str, messages: list[dict[str, Any]]) -> Any:
        response = await super()._query_with_retry(model, messages)
        try:
            await self._econo.on_response(response, messages)
        except Exception as exc:
            self._econo.error("query", exc)
        return response

    async def run(self, instruction: str, environment: Any, context: Any) -> None:
        try:
            await super().run(instruction, environment, context)
        finally:
            try:
                await self._econo.finish(environment)
            except Exception as exc:
                self._econo.error("finish", exc)
