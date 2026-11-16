"""Custom read-only static-analysis MCP server (bounded reads, deterministic rules)."""

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from operyxa.mcp_servers import analysis_rules as rules
from operyxa.mcp_servers.github_client import GitHubClient, GitHubError

MAX_FILES_PER_SCAN = 12


def build_analysis_server(client: GitHubClient) -> MCPServer:
    server = MCPServer("operyxa-analysis")

    async def tree_paths(owner: str, repo: str, ref: str) -> set[str]:
        return {e["path"] for e in await client.get_tree(owner, repo, ref) if e["type"] == "file"}

    @server.tool()
    async def scan_dependencies(owner: str, repo: str, ref: str) -> dict:
        """Parse dependency manifests; report unpinned or unsafe specs and missing lockfiles."""
        try:
            paths = await tree_paths(owner, repo, ref)
            manifests = sorted(p for p in paths if rules.is_manifest(p))[:MAX_FILES_PER_SCAN]
            result, issues = [], []
            for path in manifests:
                deps, found = rules.parse_manifest(
                    path, await client.get_file(owner, repo, ref, path)
                )
                result.append(
                    {"path": path, "dependency_count": len(deps), "dependencies": deps[:80]}
                )
                issues.extend(found)
            issues.extend(rules.lockfile_issues(paths, manifests))
            return {"manifests": result, "issues": issues}
        except GitHubError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool()
    async def check_config(owner: str, repo: str, ref: str) -> dict:
        """Check repository hygiene: CI, tests, committed secrets files, Dockerfiles, workflows."""
        try:
            paths = await tree_paths(owner, repo, ref)
            issues = rules.tree_issues(paths)
            dockerfiles = [
                p for p in sorted(paths) if p.rsplit("/", 1)[-1].startswith("Dockerfile")
            ]
            workflows = [
                p
                for p in sorted(paths)
                if p.startswith(".github/workflows/") and p.endswith((".yml", ".yaml"))
            ]
            checked = (dockerfiles + workflows)[:MAX_FILES_PER_SCAN]
            for path in checked:
                text = await client.get_file(owner, repo, ref, path)
                check = rules.dockerfile_issues if path in dockerfiles else rules.workflow_issues
                issues.extend(check(path, text))
            return {"files_in_tree": len(paths), "files_checked": checked, "issues": issues}
        except GitHubError as exc:
            raise ToolError(str(exc)) from exc

    return server
