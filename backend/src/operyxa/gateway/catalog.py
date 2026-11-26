"""Trusted tool catalog and authorization policy.

MCP is a tool *interface*, not a security policy. A tool is callable only if it is listed here:
anything else a server advertises is ignored. The decision is pure server-side code and never
takes the model's opinion as input.
"""

import fnmatch
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

Risk = Literal["low", "moderate", "high", "critical"]
Decision = Literal["allow", "deny", "approval_required"]
# Project-level override (F-10). It can only make a tool stricter, never looser.
PolicyMode = Literal["default", "approval_required", "disabled"]
POLICY_MODES = ("default", "approval_required", "disabled")
_STRICTNESS = {"allow": 0, "approval_required": 1, "deny": 2}

# Parameters the gateway injects from the run's scope. They are stripped from the schema shown
# to the model, so the model cannot even express "another repository".
SCOPE_PARAMS = ("owner", "repo", "ref", "token")

# server -> tool -> (risk, agents allowed to call it)
CATALOG: dict[str, dict[str, tuple[Risk, frozenset[str]]]] = {
    "github": {
        "get_repo": ("low", frozenset({"system", "repository"})),
        "list_tree": ("low", frozenset({"repository", "security", "remediation"})),
        "get_file": ("low", frozenset({"repository", "security", "remediation"})),
        "list_commits": ("low", frozenset({"repository"})),
        "get_commit_diff": ("low", frozenset({"repository"})),
    },
    # Records a proposed file change and returns its diff. It writes nothing: a pull request is
    # only created by the server after a person confirms it (F-12).
    "changes": {
        "propose_file_change": ("low", frozenset({"remediation"})),
    },
    # Executes the repository's own (untrusted) code in a sandbox: always behind an approval.
    "test_runner": {
        "list_test_profiles": ("low", frozenset({"testing"})),
        "run_test_profile": ("high", frozenset({"testing"})),
    },
    "analysis": {
        "scan_dependencies": ("moderate", frozenset({"security"})),
        "check_config": ("moderate", frozenset({"security"})),
        "scan_vulnerabilities": ("moderate", frozenset({"security"})),
    },
}

SENSITIVE_PATH_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    ".npmrc",
    ".netrc",
    ".pypirc",
    "credentials",
    "credentials.*",
    "*.tfstate",
)
_ENV_TEMPLATES = (".example", ".sample", ".template", ".dist")


@dataclass(frozen=True)
class ToolDef:
    name: str  # "github.get_file"
    server: str
    remote_name: str
    description: str
    input_schema: dict[str, Any]  # model-facing: scope parameters removed
    scope_params: tuple[str, ...]
    risk: Risk
    agents: frozenset[str]

    @property
    def model_name(self) -> str:
        return self.name.replace(".", "__")


def base_decision(risk: str) -> Decision:
    return {"critical": "deny", "high": "approval_required"}.get(risk, "allow")  # type: ignore[return-value]


def effective_decision(risk: str, mode: str | None) -> Decision:
    """Catalog decision tightened by the project override. Unknown modes count as disabled."""
    base = base_decision(risk)
    wanted: Decision = {None: base, "default": base, "approval_required": "approval_required"}.get(
        mode, "deny"
    )  # type: ignore[assignment]
    return base if _STRICTNESS[base] >= _STRICTNESS[wanted] else wanted


def decide(
    tool: ToolDef, agent: str, policy: Mapping[str, str] | None = None
) -> tuple[Decision, str]:
    if agent not in tool.agents:
        return "deny", f"agent '{agent}' is not allowed to call {tool.name}"
    if tool.risk == "critical":
        return "deny", "critical tools are disabled"
    mode = (policy or {}).get(tool.name)
    decision = effective_decision(tool.risk, mode)
    if decision == "deny":
        return "deny", "this tool is disabled for this project"
    if decision == "approval_required":
        if tool.risk == "high":
            return "approval_required", "this tool needs an explicit human approval"
        return "approval_required", "this project requires a human approval for this tool"
    return "allow", "allowed by policy"


def is_sensitive_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].lower()
    if name.startswith(".env") and name.endswith(_ENV_TEMPLATES):
        return False
    return any(fnmatch.fnmatch(name, pattern) for pattern in SENSITIVE_PATH_PATTERNS)


def catalog_tools() -> set[str]:
    return {f"{server}.{name}" for server, tools in CATALOG.items() for name in tools}


def describe_catalog(policy: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """View for the API (MCP Hub): tools, risk, catalog policy and, for a project, its override."""
    out = []
    for server, tools in CATALOG.items():
        for name, (risk, agents) in tools.items():
            tool = f"{server}.{name}"
            mode = (policy or {}).get(tool, "default")
            entry = {
                "server": server,
                "tool": tool,
                "risk": risk,
                "policy": base_decision(risk),
                "agents": sorted(agents - {"system"}),
                "access": "execute" if risk == "high" else "read-only",
            }
            if policy is not None:
                entry["mode"] = mode
                entry["effective"] = effective_decision(risk, mode)
                # The run setup needs get_repo to pin the commit: it cannot be switched off.
                entry["configurable"] = tool != "github.get_repo"
            out.append(entry)
    return out
