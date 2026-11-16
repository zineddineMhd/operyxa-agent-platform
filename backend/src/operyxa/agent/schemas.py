"""Structured outputs. The model fills them through forced tool calls; the server validates."""

from typing import Any, Literal

from pydantic import BaseModel, Field

AgentName = Literal["repository", "security"]
Severity = Literal["critical", "high", "medium", "low", "info"]
Category = Literal[
    "architecture", "quality", "dependencies", "security", "testing", "maintainability", "history"
]


class PlanStep(BaseModel):
    id: str = Field(
        pattern=r"^[a-z][a-z0-9_]{0,39}$",
        description="short snake_case identifier, at most 40 characters, e.g. repo_overview",
    )
    agent: AgentName
    action: str = Field(min_length=5, max_length=600)
    depends_on: list[str] = []


class ExecutionPlan(BaseModel):
    steps: list[PlanStep] = Field(min_length=1)


class EvidenceRef(BaseModel):
    evidence_id: str = Field(description="evidence_id of the tool result that proves the claim")
    quote: str = Field(
        min_length=3,
        max_length=600,
        description="short excerpt copied verbatim from that tool result",
    )


class FindingDraft(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    severity: Severity
    category: Category
    description: str = Field(min_length=3, max_length=2000)
    recommendation: str = Field(default="", max_length=1000)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=5)


class StepResult(BaseModel):
    summary: str = Field(min_length=3, max_length=3000)
    findings: list[FindingDraft] = Field(default=[], max_length=15)


class ReportDraft(BaseModel):
    summary: str = Field(min_length=3, max_length=3000)
    recommendations: list[str] = Field(default=[], max_length=8)


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
        # Drop JSON-schema metadata only: a *property* may legitimately be named "title".
        return {
            k: _inline(v, defs)
            for k, v in node.items()
            if k != "$defs" and not (k == "title" and isinstance(v, str))
        }
    if isinstance(node, list):
        return [_inline(v, defs) for v in node]
    return node


def tool_spec(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


def schema_tool(name: str, description: str, model: type[BaseModel]) -> dict[str, Any]:
    """A function-calling spec with `$ref`s inlined (not every provider resolves them)."""
    schema = model.model_json_schema()
    return tool_spec(name, description, _inline(schema, schema.get("$defs", {})))
