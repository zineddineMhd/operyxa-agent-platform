"""Read-only GitHub MCP server.

A deliberately small surface (5 GET-only tools) instead of a general-purpose GitHub server:
what is not exposed cannot be abused by a prompt-injected model.
"""

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from operyxa.mcp_servers.github_client import GitHubClient, GitHubError


def build_github_server(client: GitHubClient) -> MCPServer:
    server = MCPServer("operyxa-github")

    async def guarded(coro) -> Any:
        try:
            return await coro
        except GitHubError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool()
    async def get_repo(owner: str, repo: str) -> dict:
        """Repository metadata: description, default branch, main language, head commit."""
        return await guarded(client.get_repo(owner, repo))

    @server.tool()
    async def list_tree(
        owner: str, repo: str, ref: str, path_prefix: str = "", max_entries: int = 400
    ) -> dict:
        """List files and directories of the repository, optionally under a path prefix."""
        entries = await guarded(client.get_tree(owner, repo, ref))
        prefix = path_prefix.strip("/")
        if prefix:
            entries = [e for e in entries if e["path"].startswith(prefix + "/")]
        limit = max(1, min(max_entries, 1000))
        return {
            "total": len(entries),
            "truncated": len(entries) > limit,
            "entries": entries[:limit],
        }

    @server.tool()
    async def get_file(
        owner: str, repo: str, ref: str, path: str, start_line: int = 1, max_lines: int = 300
    ) -> dict:
        """Read a text file (or a window of it). Lines are 1-based."""
        text = await guarded(client.get_file(owner, repo, ref, path))
        lines = text.splitlines()
        start = max(1, start_line)
        window = lines[start - 1 : start - 1 + max(1, min(max_lines, 600))]
        return {
            "path": path,
            "start_line": start,
            "total_lines": len(lines),
            "truncated": start - 1 + len(window) < len(lines),
            "content": "\n".join(window),
        }

    @server.tool()
    async def list_commits(owner: str, repo: str, ref: str, limit: int = 5) -> dict:
        """Most recent commits reachable from the analysed revision."""
        commits = await guarded(client.list_commits(owner, repo, ref, max(1, min(limit, 30))))
        return {"commits": commits}

    @server.tool()
    async def get_commit_diff(owner: str, repo: str, sha: str) -> dict:
        """Files changed by one commit, with (truncated) patches."""
        return await guarded(client.get_commit(owner, repo, sha))

    return server
