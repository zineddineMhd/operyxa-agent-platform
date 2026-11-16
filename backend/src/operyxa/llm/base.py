"""Provider-neutral LLM interface plus the metering wrapper that enforces run budgets."""

import asyncio
import logging
import time
import uuid
from collections import deque
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage

from operyxa.agent.budget import RunBudget
from operyxa.agent.store import RunStore
from operyxa.errors import LLMUnavailable

log = logging.getLogger(__name__)


class LLMClient(Protocol):
    model_name: str

    async def invoke(
        self,
        messages: list[BaseMessage],
        tools: list[dict[str, Any]],
        tool_choice: str | None = None,
    ) -> AIMessage: ...


_TRANSIENT = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "UNAVAILABLE",
    "RESOURCE_EXHAUSTED",
    "DEADLINE_EXCEEDED",
    "INTERNAL",
    "timeout",
    "timed out",
    "overloaded",
)


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    text = str(exc)
    return "INVALID_ARGUMENT" not in text and any(marker in text for marker in _TRANSIENT)


def _is_quota(exc: Exception) -> bool:
    return "RESOURCE_EXHAUSTED" in str(exc) or "429" in str(exc)


class RateLimiter:
    """Sliding one-minute window. Waiting here is cheaper than burning retries on HTTP 429."""

    def __init__(self, per_minute: int):
        self._limit = per_minute
        self._stamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self._limit <= 0:
            return
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._stamps and now - self._stamps[0] >= 60:
                    self._stamps.popleft()
                if len(self._stamps) < self._limit:
                    self._stamps.append(now)
                    return
                await asyncio.sleep(60 - (now - self._stamps[0]) + 0.05)


class ResilientLLM:
    """Retries transient provider failures with backoff, then moves to the next model.

    Provider overload (HTTP 503/429) is routine; it must not fail a run on the first error.
    """

    def __init__(
        self,
        clients: list[LLMClient],
        max_retries: int,
        base_seconds: float,
        timeout_seconds: float = 90.0,
        requests_per_minute: int = 0,
        quota_wait_seconds: float = 30.0,
    ):
        self._quota_wait = quota_wait_seconds
        self._clients = clients
        self._limiters = [RateLimiter(requests_per_minute) for _ in clients]
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._base = base_seconds
        self.model_name = clients[0].model_name

    async def invoke(
        self,
        messages: list[BaseMessage],
        tools: list[dict[str, Any]],
        tool_choice: str | None = None,
    ) -> AIMessage:
        last: Exception | None = None
        for client, limiter in zip(self._clients, self._limiters, strict=True):
            for attempt in range(self._max_retries + 1):
                await limiter.acquire()
                try:
                    async with asyncio.timeout(self._timeout):
                        ai = await client.invoke(messages, tools, tool_choice)
                    ai.response_metadata.setdefault("served_by", client.model_name)
                    return ai
                except Exception as exc:  # provider SDKs raise many unrelated types
                    if not _is_transient(exc):
                        raise  # a rejected request will be rejected again: fail fast
                    last = exc
                    log.warning(
                        "%s failed (attempt %d): %s", client.model_name, attempt + 1, str(exc)[:200]
                    )
                    if _is_quota(exc):
                        if client is not self._clients[-1]:
                            break  # this model's quota is spent: use the next one
                        if attempt < self._max_retries:
                            await asyncio.sleep(self._quota_wait)  # per-minute window
                        continue
                    if attempt < self._max_retries:
                        await asyncio.sleep(min(self._base * 2**attempt, 20.0))
        raise LLMUnavailable(str(last)[:300]) from last


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
        self._pricing = pricing

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
        model = ai.response_metadata.get("served_by", self._inner.model_name)
        price = self._pricing.get(model, {})
        usage = ai.usage_metadata or {}
        tokens_in = int(usage.get("input_tokens", 0))
        tokens_out = int(usage.get("output_tokens", 0))
        cost = (tokens_in * price.get("input", 0.0) + tokens_out * price.get("output", 0.0)) / 1e6
        self._budget.add_cost(cost)
        await self._store.record_usage(
            run_id=self._run_id,
            step_key=step_key,
            model=model,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            estimated_cost_usd=cost,
        )
        return ai
