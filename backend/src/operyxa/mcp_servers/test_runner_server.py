"""Test-runner MCP server: runs a server-defined test profile in an isolated sandbox.

`run_test_profile` executes untrusted code, so the gateway classifies it as high risk: it only
runs after a person approved this exact call (profile + pinned commit). The model chooses a
profile name, never a command.
"""

from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from operyxa.mcp_servers.github_client import GitHubClient, GitHubError
from operyxa.mcp_servers.sandbox import PROFILES, SandboxError, SandboxRunner, applicable_profiles

ProfileName = Literal[tuple(PROFILES)]  # type: ignore[valid-type]  # becomes a JSON-schema enum


def build_test_runner_server(
    base: GitHubClient, sandbox: SandboxRunner, max_archive_bytes: int
) -> MCPServer:
    server = MCPServer("operyxa-test-runner")

    @server.tool()
    async def list_test_profiles(owner: str, repo: str, ref: str, token: str = "") -> dict:
        """Test profiles the platform can run, and which ones match this repository."""
        try:
            tree = await base.with_token(token).get_tree(owner, repo, ref)
        except GitHubError as exc:
            raise ToolError(str(exc)) from exc
        root = {e["path"] for e in tree if e["type"] == "file" and "/" not in e["path"]}
        matching = set(applicable_profiles(root))
        return {
            "profiles": [
                {
                    "name": p.name,
                    "description": p.description,
                    "applicable": p.name in matching,
                    "detected_by": sorted(f for f in p.detect if f in root),
                }
                for p in PROFILES.values()
            ],
            "note": "Running a profile requires a human approval and happens in a sandbox "
            "without network access or secrets.",
        }

    @server.tool()
    async def run_test_profile(
        profile: ProfileName, owner: str, repo: str, ref: str, token: str = ""
    ) -> dict:
        """Run one test profile against the analysed commit in an isolated sandbox.
        Returns the status, test counts, failing tests and the tail of the output."""
        try:
            archive = await base.with_token(token).get_tarball(owner, repo, ref, max_archive_bytes)
            result = await sandbox.run(profile, archive)
        except (GitHubError, SandboxError) as exc:
            raise ToolError(str(exc)) from exc
        return {"commit": ref, **result}

    return server
