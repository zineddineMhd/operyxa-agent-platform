"""Per-run budgets. Checked by server code before every LLM call and every tool call."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from operyxa.agent.store import RunStore
from operyxa.db.models import aware, utcnow
from operyxa.errors import BudgetExceeded, RunCancelled


@dataclass
class Limits:
    max_llm_calls: int
    max_tool_calls: int
    max_seconds: int
    max_cost_usd: float


class RunBudget:
    def __init__(
        self,
        run_id: uuid.UUID,
        limits: Limits,
        store: RunStore,
        started_at: datetime,
        counters: dict[str, float],
    ):
        self.run_id = run_id
        self.limits = limits
        self._store = store
        self._started_at = aware(started_at)
        self.llm_calls = int(counters.get("llm_calls", 0))
        self.tool_calls = int(counters.get("tool_calls", 0))
        self.cost_usd = float(counters.get("cost_usd", 0.0))
        self._paused = 0.0  # time spent waiting for a human does not count as run time

    async def _common(self) -> None:
        if await self._store.cancel_requested(self.run_id):
            raise RunCancelled()
        elapsed = (utcnow() - self._started_at).total_seconds() - self._paused
        if elapsed > self.limits.max_seconds:
            raise BudgetExceeded("time")
        if self.cost_usd >= self.limits.max_cost_usd:
            raise BudgetExceeded("cost")

    async def reserve_llm_call(self) -> None:
        await self._common()
        if self.llm_calls >= self.limits.max_llm_calls:
            raise BudgetExceeded("llm_calls")
        self.llm_calls += 1

    async def reserve_tool_call(self) -> None:
        await self._common()
        if self.tool_calls >= self.limits.max_tool_calls:
            raise BudgetExceeded("tool_calls")
        self.tool_calls += 1

    def add_pause(self, seconds: float) -> None:
        self._paused += seconds

    def add_cost(self, usd: float) -> None:
        self.cost_usd += usd
