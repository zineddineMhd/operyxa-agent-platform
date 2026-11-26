"""Fix proposals MCP server (F-12). It writes nothing.

`propose_file_change` validates a proposed new version of one file and returns its unified
diff. The gateway stores the call like any other evidence; a pull request is assembled from
these records only when a person asks for it, and created only after they confirm it.
"""

import difflib
import fnmatch
import hashlib

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from operyxa.gateway.redaction import MARKER, redact
from operyxa.mcp_servers.github_client import GitHubClient, GitHubError, check_path

MAX_CONTENT_CHARS = 60_000
MAX_DIFF_CHARS = 20_000
# Changing these would let a proposal run code with the repository's CI secrets, or hide
# changes from review: they are left to a person.
PROTECTED_PATHS = (
    ".github/workflows/*",
    ".github/actions/*",
    ".gitmodules",
    "CODEOWNERS",
    ".github/CODEOWNERS",
)


def unified_diff(path: str, old: str | None, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            (old or "").splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="/dev/null" if old is None else f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def build_changes_server(base: GitHubClient) -> MCPServer:
    server = MCPServer("operyxa-changes")

    @server.tool()
    async def propose_file_change(
        owner: str,
        repo: str,
        ref: str,
        path: str,
        new_content: str,
        rationale: str,
        token: str = "",
    ) -> dict:
        """Propose the full new content of ONE file to fix a verified issue. Nothing is written:
        a person reviews the diff and decides whether a pull request is created."""
        try:
            path = check_path(path)
        except GitHubError as exc:
            raise ToolError(str(exc)) from exc
        if any(fnmatch.fnmatch(path, p) for p in PROTECTED_PATHS):
            raise ToolError(
                "PATH_PROTECTED: CI workflows and ownership files are changed by a person"
            )
        if len(new_content) > MAX_CONTENT_CHARS:
            raise ToolError(f"TOO_LARGE: proposals are limited to {MAX_CONTENT_CHARS} characters")
        if MARKER.search(new_content) or redact(new_content) != new_content:
            raise ToolError("CONTAINS_SECRET: the new content holds a secret or a redaction marker")
        client = base.with_token(token)
        try:
            original: str | None = await client.get_file(owner, repo, ref, path)
        except GitHubError as exc:
            if exc.code != "NOT_FOUND":
                raise ToolError(str(exc)) from exc
            original = None
        if original is not None and redact(original) != original:
            # The model only saw a masked copy: rewriting the file would destroy the real value.
            raise ToolError("FILE_HAS_SECRETS: files that hold secrets must be fixed by a person")
        if original == new_content:
            raise ToolError("NO_CHANGE: the proposed content is identical to the current file")
        diff = unified_diff(path, original, new_content)
        if len(diff) > MAX_DIFF_CHARS:
            raise ToolError("TOO_LARGE: keep proposals small and focused")
        return {
            "path": path,
            "change_type": "add" if original is None else "modify",
            "rationale": rationale[:1000],
            "diff": diff,
            "new_content": new_content,
            "base_sha256": hashlib.sha256((original or "").encode()).hexdigest(),
        }

    return server
