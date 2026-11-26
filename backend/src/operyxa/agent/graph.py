"""LangGraph state machine: Plan-and-Execute around bounded ReAct steps.

    planner -> supervisor -> agent_step (parallel fan-out) -> supervisor -> ...
                          -> verifier -> reporter -> END

The supervisor is deterministic code: once a plan is validated, routing is a pure function of
the dependency graph, so no LLM call (and no hallucinated routing) is spent on it.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import ValidationError

from operyxa.agent import prompts
from operyxa.agent.budget import RunBudget
from operyxa.agent.dedup import find_duplicates, merged_evidence
from operyxa.agent.evidence import verify_finding
from operyxa.agent.plan import validate_plan
from operyxa.agent.react import StepInput, run_step
from operyxa.agent.report import build_body, finding_view, render_markdown
from operyxa.agent.schemas import ExecutionPlan, ReportDraft, schema_tool
from operyxa.agent.store import RunStore
from operyxa.config import Settings
from operyxa.db.models import utcnow
from operyxa.errors import BudgetExceeded, LLMUnavailable, RunCancelled
from operyxa.gateway.gateway import CallContext, ToolGateway
from operyxa.llm.base import MeteredLLM

log = logging.getLogger(__name__)
BASE_AGENTS = frozenset({"repository", "security"})
MAX_PLAN_ATTEMPTS = 2


def turns_per_step(max_llm_calls: int, steps: int, cap: int) -> int:
    """Share the run's model-call budget between the steps of the plan.

    Two calls are reserved for the planner and the reporter. Without this, the first steps of a
    long plan spend the whole budget and the last ones never run. The final turn of a step forces
    the structured result, so a step always concludes within its share.
    """
    return max(2, min(cap, (max_llm_calls - 2) // max(steps, 1)))


def _merge(left: dict, right: dict) -> dict:
    return {**left, **right}


def _first(left: str | None, right: str | None) -> str | None:
    return left or right


class RunState(TypedDict, total=False):
    plan: dict | None
    plan_errors: list[str]
    plan_attempts: int
    # Reducers make parallel branches safe: each branch contributes its own keys.
    step_results: Annotated[dict[str, dict[str, Any]], _merge]
    stop_reason: Annotated[str | None, _first]


@dataclass
class RunDeps:
    run_id: uuid.UUID
    actor_id: uuid.UUID
    objective: str
    scope: dict[str, str]
    settings: Settings
    store: RunStore
    gateway: ToolGateway
    llm: MeteredLLM
    budget: RunBudget
    # Agents the planner may use for this run: the base set, plus "testing" when the run asked
    # for tests and a sandboxed test runner is connected.
    agents: frozenset[str] = BASE_AGENTS
    # Project tool-policy overrides frozen on the run (F-10).
    policy: dict[str, str] = field(default_factory=dict)


STOPPING = (BudgetExceeded, RunCancelled, LLMUnavailable)


def _stop_reason(exc: Exception) -> str:
    if isinstance(exc, RunCancelled):
        return "cancelled"
    if isinstance(exc, LLMUnavailable):
        return "llm_unavailable"
    return f"budget_exceeded:{exc.kind}"


def build_graph(deps: RunDeps, checkpointer: Any):
    store, settings, run_id = deps.store, deps.settings, deps.run_id

    async def planner(state: RunState) -> dict[str, Any]:
        attempts = state.get("plan_attempts", 0) + 1
        request: dict[str, Any] = {
            "objective": deps.objective,
            "available_agents": sorted(deps.agents),
        }
        if state.get("plan_errors"):
            request["previous_errors"] = state["plan_errors"]
        try:
            ai = await deps.llm.invoke(
                [
                    SystemMessage(prompts.PLANNER.format(max_steps=settings.max_plan_steps)),
                    HumanMessage(json.dumps(request, ensure_ascii=False)),
                ],
                [schema_tool("submit_plan", "Submit the execution plan.", ExecutionPlan)],
                "submit_plan",
                step_key="planner",
            )
        except STOPPING as exc:
            return {"stop_reason": _stop_reason(exc), "plan_attempts": attempts}
        raw = ai.tool_calls[0]["args"] if ai.tool_calls else {}
        plan, errors = validate_plan(
            raw, max_steps=settings.max_plan_steps, allowed_agents=set(deps.agents)
        )
        if errors:
            await store.emit(run_id, "plan.rejected", {"errors": errors, "attempt": attempts})
            update: dict[str, Any] = {
                "plan": None,
                "plan_errors": errors,
                "plan_attempts": attempts,
            }
            if attempts >= MAX_PLAN_ATTEMPTS:
                update["stop_reason"] = "plan_invalid"
            return update
        await store.save_plan(run_id, plan)
        await store.emit(run_id, "plan.validated", {"plan": plan})
        return {"plan": plan, "plan_errors": [], "plan_attempts": attempts}

    def after_planner(state: RunState) -> str:
        if state.get("stop_reason"):
            return "verifier"
        return "supervisor" if state.get("plan") else "planner"

    async def supervisor(state: RunState) -> dict[str, Any]:
        """Mark steps unreachable because an upstream step failed or was skipped."""
        results = dict(state.get("step_results", {}))
        skipped: dict[str, dict[str, Any]] = {}
        changed = True
        while changed:
            changed = False
            for step in state["plan"]["steps"]:
                if step["id"] in results:
                    continue
                blockers = [
                    d
                    for d in step["depends_on"]
                    if results.get(d, {}).get("status") in ("failed", "skipped")
                ]
                if blockers:
                    entry = {
                        "status": "skipped",
                        "summary": f"skipped: {blockers[0]} did not complete",
                    }
                    results[step["id"]] = skipped[step["id"]] = entry
                    await store.update_step(
                        run_id,
                        step["id"],
                        status="skipped",
                        error=entry["summary"],
                        completed_at=utcnow(),
                    )
                    await store.emit(
                        run_id, "step.skipped", {"step": step["id"], "blocked_by": blockers}
                    )
                    changed = True
        return {"step_results": skipped}

    def dispatch(state: RunState) -> Any:
        if state.get("stop_reason"):
            return "verifier"
        results = state.get("step_results", {})
        ready = [
            s
            for s in state["plan"]["steps"]
            if s["id"] not in results
            and all(results.get(d, {}).get("status") == "completed" for d in s["depends_on"])
        ]
        if not ready:
            return "verifier"
        return [
            Send(
                "agent_step",
                {
                    "step": s,
                    "dependencies": {d: results[d].get("summary", "") for d in s["depends_on"]},
                    "max_turns": turns_per_step(
                        deps.budget.limits.max_llm_calls,
                        len(state["plan"]["steps"]),
                        settings.max_turns_per_step,
                    ),
                },
            )
            for s in ready[: settings.max_parallel_steps]
        ]

    async def agent_step(payload: dict[str, Any]) -> dict[str, Any]:
        step = payload["step"]
        key, agent = step["id"], step["agent"]
        await store.update_step(run_id, key, status="running", started_at=utcnow(), error=None)
        await store.emit(run_id, "agent.started", {"step": key, "agent": agent})
        ctx = CallContext(run_id, key, agent, deps.scope, deps.budget, deps.actor_id, deps.policy)
        update: dict[str, Any] = {}
        try:
            result = await run_step(
                StepInput(key, agent, step["action"], deps.objective, payload["dependencies"]),
                llm=deps.llm,
                gateway=deps.gateway,
                ctx=ctx,
                max_turns=payload.get("max_turns", settings.max_turns_per_step),
            )
        except STOPPING as exc:
            error, update["stop_reason"] = str(exc) or "cancelled", _stop_reason(exc)
        except (ValidationError, RuntimeError) as exc:
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
        except Exception as exc:  # provider or infrastructure failure inside the step
            log.exception("step %s failed", key)
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
        else:
            await store.replace_findings(
                run_id, key, agent, [f.model_dump() for f in result.findings]
            )
            await store.update_step(
                run_id, key, status="completed", summary=result.summary, completed_at=utcnow()
            )
            await store.emit(
                run_id,
                "agent.completed",
                {"step": key, "agent": agent, "findings": len(result.findings)},
            )
            return {"step_results": {key: {"status": "completed", "summary": result.summary}}}
        await store.update_step(run_id, key, status="failed", error=error, completed_at=utcnow())
        await store.emit(run_id, "agent.failed", {"step": key, "agent": agent, "error": error})
        update["step_results"] = {key: {"status": "failed", "summary": error}}
        return update

    async def verifier(state: RunState) -> dict[str, Any]:
        calls = await store.evidence_map(run_id)
        counts = {"verified": 0, "unverified": 0}
        for finding in await store.list_findings(run_id):
            status, checked = verify_finding(finding.evidence, calls)
            counts[status] += 1
            await store.set_finding(finding.id, status, checked)
        merged = 0
        for kept, duplicates in find_duplicates(await store.list_findings(run_id)):
            await store.set_finding(kept.id, "verified", merged_evidence(kept, duplicates))
            for duplicate in duplicates:
                await store.set_finding(duplicate.id, "duplicate", duplicate.evidence, kept.id)
            merged += len(duplicates)
        counts["verified"] -= merged
        await store.emit(run_id, "findings.verified", {**counts, "duplicates_merged": merged})
        return {}

    async def reporter(state: RunState) -> dict[str, Any]:
        steps = await store.list_steps(run_id)
        findings = await store.list_findings(run_id)
        verified = [finding_view(f) for f in findings if f.status == "verified"]
        stop = state.get("stop_reason")
        incomplete = [s.step_key for s in steps if s.status != "completed"]
        if not stop and (incomplete or not steps):
            stop = "step_failed"
        summary = (
            f"{len(verified)} verified finding(s). Steps not completed: "
            f"{', '.join(incomplete) or 'none'}."
        )
        recommendations: list[str] = []
        if not state.get("stop_reason") and steps:
            payload = {
                "objective": deps.objective,
                "steps": [
                    {"id": s.step_key, "status": s.status, "summary": s.summary} for s in steps
                ],
                "verified_findings": [
                    {
                        k: f[k]
                        for k in ("title", "severity", "category", "description", "recommendation")
                    }
                    for f in verified
                ],
            }
            try:
                ai = await deps.llm.invoke(
                    [
                        SystemMessage(prompts.REPORTER),
                        HumanMessage(json.dumps(payload, ensure_ascii=False)),
                    ],
                    [schema_tool("submit_report", "Submit the report summary.", ReportDraft)],
                    "submit_report",
                    step_key="reporter",
                )
                draft = ReportDraft.model_validate(
                    ai.tool_calls[0]["args"] if ai.tool_calls else {}
                )
                summary, recommendations = draft.summary, draft.recommendations
            except (BudgetExceeded, RunCancelled) as exc:
                stop = _stop_reason(exc)
            except Exception:  # the deterministic summary above is the fallback
                log.exception("report summary failed; using the deterministic fallback")
        body = build_body(
            objective=deps.objective,
            repository=f"{deps.scope['owner']}/{deps.scope['repo']}",
            commit_sha=deps.scope["ref"],
            summary=summary,
            recommendations=recommendations,
            steps=steps,
            findings=findings,
            stop_reason=stop,
            counters=await store.counters(run_id),
        )
        await store.save_report(
            run_id,
            partial=stop is not None,
            summary=summary,
            body=body,
            markdown=render_markdown(body),
        )
        await store.emit(
            run_id,
            "report.ready",
            {"partial": stop is not None, "verified_findings": len(verified)},
        )
        return {"stop_reason": stop}

    graph = StateGraph(RunState)
    graph.add_node("planner", planner)
    graph.add_node("supervisor", supervisor)
    graph.add_node("agent_step", agent_step)
    graph.add_node("verifier", verifier)
    graph.add_node("reporter", reporter)
    graph.add_edge(START, "planner")
    graph.add_conditional_edges("planner", after_planner, ["planner", "supervisor", "verifier"])
    graph.add_conditional_edges("supervisor", dispatch, ["agent_step", "verifier"])
    graph.add_edge("agent_step", "supervisor")
    graph.add_edge("verifier", "reporter")
    graph.add_edge("reporter", END)
    return graph.compile(checkpointer=checkpointer)
