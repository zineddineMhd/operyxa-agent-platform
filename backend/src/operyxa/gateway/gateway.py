"""The single choke point between agents and tools.

Order of checks for every call: trusted catalog -> agent allowlist / risk policy -> argument
schema -> resource scope (injected, not requested) -> sensitive paths -> budget -> execution
with timeout, bounded retries and a circuit breaker -> redaction and size cap -> audit trail.
"""

import asyncio
import hashlib
import json
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

import jsonschema

from operyxa.agent.budget import RunBudget
from operyxa.agent.store import RunStore
from operyxa.config import Settings
from operyxa.gateway.breaker import CircuitBreaker
from operyxa.gateway.catalog import CATALOG, SCOPE_PARAMS, ToolDef, decide, is_sensitive_path
from operyxa.gateway.providers import ToolProvider
from operyxa.gateway.redaction import redact

_ERROR_CODE = re.compile(r"\b([A-Z][A-Z_]{2,}): (.*)$", re.S)
RETRYABLE = {"UPSTREAM_UNAVAILABLE", "TOOL_TIMEOUT", "TOOL_TRANSPORT_ERROR"}


@dataclass(frozen=True)
class CallContext:
    run_id: uuid.UUID
    step_key: str
    agent: str
    scope: dict[str, str]  # owner, repo, ref — fixed by the run, never by the model
    budget: RunBudget | None = None
    actor_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ToolOutcome:
    ok: bool
    tool: str
    evidence_id: str | None
    text: str
    error_code: str | None = None

    def for_model(self) -> str:
        """Tool output is data, not instructions: it is fenced and labelled as untrusted."""
        if not self.ok:
            return (
                f'<tool_error tool="{self.tool}" code="{self.error_code}">{self.text}</tool_error>'
            )
        body = self.text.replace("</tool_result", "<\\/tool_result")
        return (
            f'<tool_result evidence_id="{self.evidence_id}" tool="{self.tool}" '
            f'trust="untrusted_data">\n{body}\n</tool_result>'
        )


class ToolGateway:
    def __init__(self, settings: Settings, store: RunStore, providers: list[ToolProvider]):
        self._settings = settings
        self._store = store
        self._providers = {p.name: p for p in providers}
        self._breakers = {
            p.name: CircuitBreaker(
                settings.breaker_failure_threshold, settings.breaker_reset_seconds
            )
            for p in providers
        }
        self._tools: dict[str, ToolDef] = {}

    async def discover(self) -> None:
        """Intersect what servers advertise with the trusted catalog. Unknown tools are dropped."""
        tools: dict[str, ToolDef] = {}
        for name, provider in self._providers.items():
            trusted = CATALOG.get(name, {})
            for remote in await provider.list_tools():
                if remote.name not in trusted:
                    continue
                risk, agents = trusted[remote.name]
                properties = dict(remote.input_schema.get("properties", {}))
                scope = tuple(p for p in SCOPE_PARAMS if p in properties)
                for param in scope:
                    properties.pop(param)
                schema = {
                    "type": "object",
                    "properties": {
                        k: {kk: vv for kk, vv in v.items() if kk != "title"}
                        for k, v in properties.items()
                    },
                    "required": [
                        r for r in remote.input_schema.get("required", []) if r not in scope
                    ],
                    "additionalProperties": False,
                }
                tool = ToolDef(
                    f"{name}.{remote.name}",
                    name,
                    remote.name,
                    remote.description,
                    schema,
                    scope,
                    risk,
                    agents,
                )
                tools[tool.name] = tool
        self._tools = tools

    def tools_for(self, agent: str) -> list[ToolDef]:
        return [
            t for t in self._tools.values() if agent in t.agents and decide(t, agent)[0] == "allow"
        ]

    def resolve_model_name(self, model_name: str) -> str:
        return model_name.replace("__", ".")

    async def call(self, ctx: CallContext, tool_name: str, args: dict[str, Any]) -> ToolOutcome:
        args = args if isinstance(args, dict) else {}
        tool = self._tools.get(tool_name)
        if tool is None:
            return await self._reject(
                ctx,
                tool_name,
                args,
                "unknown",
                "TOOL_UNKNOWN",
                "this tool is not in the trusted catalog",
            )
        decision, reason = decide(tool, ctx.agent)
        if decision == "deny":
            return await self._reject(
                ctx, tool_name, args, tool.risk, "TOOL_PERMISSION_DENIED", reason
            )
        if decision == "approval_required":
            return await self._reject(ctx, tool_name, args, tool.risk, "APPROVAL_REQUIRED", reason)
        try:
            jsonschema.validate(args, tool.input_schema)
        except jsonschema.ValidationError as exc:
            return await self._reject(
                ctx, tool_name, args, tool.risk, "INVALID_ARGUMENTS", exc.message[:300]
            )
        path = args.get("path")
        if isinstance(path, str) and is_sensitive_path(path):
            return await self._reject(
                ctx,
                tool_name,
                args,
                tool.risk,
                "SENSITIVE_PATH_DENIED",
                "this path may hold secrets and is never read",
            )

        key = hashlib.sha256(
            json.dumps([ctx.step_key, tool_name, args], sort_keys=True).encode()
        ).hexdigest()
        cached = await self._store.find_completed_call(ctx.run_id, key)
        if cached is not None:  # resume after a crash, or a repeated identical read
            return ToolOutcome(True, tool_name, cached.evidence_id, cached.result_text or "")

        if ctx.budget is not None:
            await ctx.budget.reserve_tool_call()
        await self._store.emit(
            ctx.run_id,
            "tool.requested",
            {
                "tool": tool_name,
                "agent": ctx.agent,
                "step": ctx.step_key,
                "args": args,
                "risk": tool.risk,
                "decision": decision,
            },
        )

        full_args = {**args, **{p: ctx.scope[p] for p in tool.scope_params}}
        started = time.monotonic()
        ok, text, code, attempts = await self._execute(tool, full_args)
        duration_ms = int((time.monotonic() - started) * 1000)

        truncated = False
        if ok:
            text = redact(text)
            limit = self._settings.tool_output_max_chars
            if len(text) > limit:
                text, truncated = text[:limit] + "\n[output truncated by gateway]", True
        evidence_id = "ev_" + secrets.token_hex(4)
        await self._store.save_tool_call(
            run_id=ctx.run_id,
            evidence_id=evidence_id,
            step_key=ctx.step_key,
            agent=ctx.agent,
            tool=tool_name,
            args=args,
            idempotency_key=key,
            risk=tool.risk,
            decision=decision,
            status="completed" if ok else "failed",
            error_code=code,
            result_text=text,
            result_hash=hashlib.sha256(text.encode()).hexdigest() if ok else None,
            truncated=truncated,
            attempts=attempts,
            duration_ms=duration_ms,
        )
        await self._store.emit(
            ctx.run_id,
            "tool.completed" if ok else "tool.failed",
            {
                "tool": tool_name,
                "agent": ctx.agent,
                "step": ctx.step_key,
                "evidence_id": evidence_id if ok else None,
                "duration_ms": duration_ms,
                "attempts": attempts,
                "error_code": code,
                "truncated": truncated,
            },
        )
        return ToolOutcome(ok, tool_name, evidence_id if ok else None, text, code)

    async def _execute(
        self, tool: ToolDef, args: dict[str, Any]
    ) -> tuple[bool, str, str | None, int]:
        provider, breaker = self._providers[tool.server], self._breakers[tool.server]
        attempts, code, message = 0, None, ""
        for attempt in range(self._settings.tool_max_retries + 1):
            if not breaker.allow():
                return (
                    False,
                    f"the '{tool.server}' connector is temporarily unavailable",
                    "TOOL_UNAVAILABLE",
                    attempts,
                )
            attempts += 1
            try:
                async with asyncio.timeout(self._settings.tool_timeout_seconds):
                    result = await provider.call(tool.remote_name, args)
                if not result.is_error:
                    breaker.success()
                    return True, result.text, None, attempts
                match = _ERROR_CODE.search(result.text)
                code = match.group(1) if match else "TOOL_ERROR"
                message = (match.group(2) if match else result.text)[:500]
            except TimeoutError:
                code, message = "TOOL_TIMEOUT", "the tool did not answer in time"
            except Exception as exc:  # transport or protocol failure
                code, message = "TOOL_TRANSPORT_ERROR", type(exc).__name__
            if code not in RETRYABLE:
                breaker.success()  # the connector answered; this is a domain error
                return False, message, code, attempts
            breaker.failure()
            if attempt < self._settings.tool_max_retries:
                await asyncio.sleep(min(0.5 * 2**attempt, 4.0))
        return False, message, code, attempts

    async def _reject(
        self,
        ctx: CallContext,
        tool_name: str,
        args: dict[str, Any],
        risk: str,
        code: str,
        reason: str,
    ) -> ToolOutcome:
        await self._store.save_tool_call(
            run_id=ctx.run_id,
            evidence_id="rj_" + secrets.token_hex(4),
            step_key=ctx.step_key,
            agent=ctx.agent,
            tool=tool_name[:128],
            args=_safe_args(args),
            idempotency_key=secrets.token_hex(16),
            risk=risk,
            decision="deny",
            status="rejected",
            error_code=code,
            result_text=reason,
        )
        await self._store.emit(
            ctx.run_id,
            "tool.rejected",
            {
                "tool": tool_name[:128],
                "agent": ctx.agent,
                "step": ctx.step_key,
                "code": code,
                "reason": reason,
            },
        )
        await self._store.audit(
            ctx.actor_id,
            "tool.rejected",
            "run",
            str(ctx.run_id),
            {"tool": tool_name[:128], "agent": ctx.agent, "code": code},
        )
        return ToolOutcome(False, tool_name, None, reason, code)


def _safe_args(args: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(redact(json.dumps(args, default=str))[:4000])
    except ValueError:
        return {"_unserialisable": True}
