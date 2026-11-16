"""Provider-neutral LLM interface plus the metering wrapper that enforces run budgets."""

import uuid
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage

from operyxa.agent.budget import RunBudget
from operyxa.agent.store import RunStore


class LLMClient(Protocol):
    model_name: str

    async def invoke(
        self,
        messages: list[BaseMessage],
        tools: list[dict[str, Any]],
        tool_choice: str | None = None,
    ) -> AIMessage: ...


class MeteredLLM:
    """Every model call of a run goes through here: budget first, usage record after."""

    def __init__(
        self,
        inner: LLMClient,
        run_id: uuid.UUID,
        budget: RunBudget,
        store: RunStore,
        pricing: dict[str, dict[str, float]],
    ):
        self._inner = inner
        self._run_id = run_id
        self._budget = budget
        self._store = store
        self._price = pricing.get(inner.model_name, {})

    async def invoke(
        self,
        messages: list[BaseMessage],
        tools: list[dict[str, Any]],
        tool_choice: str | None = None,
        *,
        step_key: str,
    ) -> AIMessage:
        await self._budget.reserve_llm_call()
        ai = await self._inner.invoke(messages, tools, tool_choice)
        usage = ai.usage_metadata or {}
        tokens_in = int(usage.get("input_tokens", 0))
        tokens_out = int(usage.get("output_tokens", 0))
        cost = (
            tokens_in * self._price.get("input", 0.0) + tokens_out * self._price.get("output", 0.0)
        ) / 1_000_000
        self._budget.add_cost(cost)
        await self._store.record_usage(
            run_id=self._run_id,
            step_key=step_key,
            model=self._inner.model_name,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            estimated_cost_usd=cost,
        )
        return ai
