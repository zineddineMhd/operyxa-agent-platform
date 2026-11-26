"""Workflow templates (F-13): a named mission with a fixed set of agents and options.

Built-in templates are code, reviewed like the tool catalog. A team can save its own workflow
on top of one of them; it may only narrow the agents of its base, never add one.
"""

from dataclasses import dataclass, field
from typing import Any

ALL_AGENTS = ("repository", "security", "testing", "remediation")


@dataclass(frozen=True)
class WorkflowTemplate:
    key: str
    name: str
    description: str
    agents: tuple[str, ...]
    objective: str
    options: dict[str, Any] = field(default_factory=dict)


BUILTIN: dict[str, WorkflowTemplate] = {
    t.key: t
    for t in (
        WorkflowTemplate(
            "repository_analysis",
            "Repository analysis",
            "Architecture, recent history, dependencies and configuration.",
            ("repository", "security"),
            "Review the architecture, the recent commits, the dependencies and the "
            "configuration of {repository}. Report evidence-backed risks.",
        ),
        WorkflowTemplate(
            "security_audit",
            "Security audit",
            "Dependencies, known vulnerabilities, CI and container configuration only.",
            ("security",),
            "Audit the dependencies, known vulnerabilities, CI workflows and container "
            "configuration of {repository}.",
        ),
        WorkflowTemplate(
            "architecture_review",
            "Architecture review",
            "Structure, key modules and recent changes, without security scanners.",
            ("repository",),
            "Map the structure and main modules of {repository} and review its recent commits.",
        ),
        WorkflowTemplate(
            "test_review",
            "Test review",
            "Runs the test suite in a sandbox (with approval) and reviews the code it covers.",
            ("repository", "testing"),
            "Run the test suite of {repository} and review the code of the failing areas.",
            {"run_tests": True},
        ),
        WorkflowTemplate(
            "remediation",
            "Review and fix proposals",
            "Security review, then small fixes proposed as a pull request you confirm.",
            ("repository", "security", "remediation"),
            "Review the dependencies and configuration of {repository}, then propose small, "
            "safe fixes for the verified issues.",
            {"propose_fixes": True},
        ),
    )
}


def describe_builtin() -> list[dict[str, Any]]:
    return [
        {
            "key": t.key,
            "name": t.name,
            "description": t.description,
            "agents": list(t.agents),
            "objective_template": t.objective,
            "options": t.options,
            "builtin": True,
        }
        for t in BUILTIN.values()
    ]


def resolve_agents(
    agents: tuple[str, ...] | list[str], *, run_tests: bool, propose_fixes: bool
) -> list[str]:
    """Agents a run may plan with. Testing and remediation exist only with their option."""
    chosen = {a for a in agents if a not in ("testing", "remediation")}
    if run_tests:
        chosen.add("testing")
    if propose_fixes:
        chosen.add("remediation")
    return sorted(chosen)
