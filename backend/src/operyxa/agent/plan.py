"""Server-side plan validation. Valid JSON is not proof that a plan is safe or feasible."""

from typing import Any

from pydantic import ValidationError

from operyxa.agent.schemas import ExecutionPlan


def validate_plan(
    raw: Any, *, max_steps: int, allowed_agents: set[str]
) -> tuple[dict | None, list[str]]:
    try:
        plan = ExecutionPlan.model_validate(raw)
    except ValidationError as exc:
        return None, [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()][
            :10
        ]

    errors: list[str] = []
    ids = [s.id for s in plan.steps]
    if len(ids) != len(set(ids)):
        errors.append("step ids must be unique")
    if len(plan.steps) > max_steps:
        errors.append(f"too many steps: {len(plan.steps)} > {max_steps}")
    for step in plan.steps:
        if step.agent not in allowed_agents:
            errors.append(f"step '{step.id}': agent '{step.agent}' is not available for this run")
        for dep in step.depends_on:
            if dep not in ids:
                errors.append(f"step '{step.id}' depends on unknown step '{dep}'")
            if dep == step.id:
                errors.append(f"step '{step.id}' depends on itself")
    if not errors and _has_cycle({s.id: list(s.depends_on) for s in plan.steps}):
        errors.append("dependencies must be acyclic")
    return (None, errors) if errors else (plan.model_dump(), [])


def _has_cycle(deps: dict[str, list[str]]) -> bool:
    remaining = {k: set(v) for k, v in deps.items()}
    while remaining:
        ready = [k for k, v in remaining.items() if not v]
        if not ready:
            return True
        for key in ready:
            del remaining[key]
        for value in remaining.values():
            value.difference_update(ready)
    return False
