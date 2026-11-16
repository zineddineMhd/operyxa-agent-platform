"""Trusted tool catalog and authorization policy.

MCP is a tool *interface*, not a security policy. A tool is callable only if it is listed here:
anything else a server advertises is ignored. The decision is pure server-side code and never
takes the model's opinion as input.
"""

import fnmatch
from dataclasses import dataclass
from typing import Any, Literal

Risk = Literal["low", "moderate", "high", "critical"]
Decision = Literal["allow", "deny", "approval_required"]

# Parameters the gateway injects from the run's scope. They are stripped from the schema shown
# to the model, so the model cannot even express "another repository".
SCOPE_PARAMS = ("owner", "repo", "ref", "token")

# server -> tool -> (risk, agents allowed to call it)
CATALOG: dict[str, dict[str, tuple[Risk, frozenset[str]]]] = {
    "github": {
        "get_repo": ("low", frozenset({"system", "repository"})),
        "list_tree": ("low", frozenset({"repository", "security"})),
        "get_file": ("low", frozenset({"repository", "security"})),
        "list_commits": ("low", frozenset({"repository"})),
        "get_commit_diff": ("low", frozenset({"repository"})),
    },
    # Not served yet: listed so the policy is in place the day a sandboxed runner is connected.
    "test_runner": {
        "run_test_profile": ("high", frozenset({"testing"})),
    },
    "analysis": {
        "scan_dependencies": ("moderate", frozenset({"security"})),
        "check_config": ("moderate", frozenset({"security"})),
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


def decide(tool: ToolDef, agent: str) -> tuple[Decision, str]:
    if agent not in tool.agents:
        return "deny", f"agent '{agent}' is not allowed to call {tool.name}"
    if tool.risk == "critical":
        return "deny", "critical tools are disabled"
    if tool.risk == "high":
        return "approval_required", "this tool needs an explicit human approval"
    return "allow", "allowed by policy"


def is_sensitive_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].lower()
    if name.startswith(".env") and name.endswith(_ENV_TEMPLATES):
        return False
    return any(fnmatch.fnmatch(name, pattern) for pattern in SENSITIVE_PATH_PATTERNS)


def describe_catalog() -> list[dict[str, Any]]:
    """Static view for the API (MCP Hub): tools, risk and resulting policy."""
    out = []
    for server, tools in CATALOG.items():
        for name, (risk, agents) in tools.items():
            policy = {"critical": "deny", "high": "approval_required"}.get(risk, "allow")
            out.append(
                {
                    "server": server,
                    "tool": f"{server}.{name}",
                    "risk": risk,
                    "policy": policy,
                    "agents": sorted(agents - {"system"}),
                    "access": "execute" if risk == "high" else "read-only",
                }
            )
    return out
