"""Custom read-only static-analysis MCP server (bounded reads, deterministic rules)."""

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from operyxa.mcp_servers import analysis_rules as rules
from operyxa.mcp_servers.github_client import GitHubClient, GitHubError
from operyxa.mcp_servers.osv import (
    MAX_ADVISORIES_PER_PACKAGE,
    MAX_PACKAGES,
    OSVClient,
    ecosystem_of,
    resolved_version,
)

MAX_FILES_PER_SCAN = 12


def build_analysis_server(base: GitHubClient, osv: OSVClient | None = None) -> MCPServer:
    server = MCPServer("operyxa-analysis")

    async def tree_paths(client: GitHubClient, owner: str, repo: str, ref: str) -> set[str]:
        return {e["path"] for e in await client.get_tree(owner, repo, ref) if e["type"] == "file"}

    @server.tool()
    async def scan_dependencies(owner: str, repo: str, ref: str, token: str = "") -> dict:
        """Parse dependency manifests; report unpinned or unsafe specs and missing lockfiles."""
        client = base.with_token(token)
        try:
            paths = await tree_paths(client, owner, repo, ref)
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
    async def scan_vulnerabilities(owner: str, repo: str, ref: str, token: str = "") -> dict:
        """Look up the declared dependency versions in the OSV advisory database (CVE, GHSA)."""
        if osv is None:
            raise ToolError("TOOL_DISABLED: vulnerability lookup is not enabled on this server")
        client = base.with_token(token)
        try:
            paths = await tree_paths(client, owner, repo, ref)
            manifests = sorted(p for p in paths if rules.is_manifest(p))[:MAX_FILES_PER_SCAN]
            targets, skipped = [], 0
            for path in manifests:
                text = await client.get_file(owner, repo, ref, path)
                lines = text.splitlines()
                for dep in rules.parse_manifest(path, text)[0]:
                    version, exact = resolved_version(dep["spec"])
                    if version is None or ecosystem_of(path) is None:
                        skipped += 1
                        continue
                    line = dep.get("line")
                    snippet = lines[line - 1].strip() if line and line <= len(lines) else None
                    targets.append((path, dep["name"], version, exact, line, snippet))
        except GitHubError as exc:
            raise ToolError(str(exc)) from exc

        checked = targets[:MAX_PACKAGES]
        results = await osv.query_many([(ecosystem_of(t[0]), t[1], t[2]) for t in checked])
        issues, failed = [], 0
        for (path, name, version, exact, line, snippet), advisories in zip(
            checked, results, strict=True
        ):
            if advisories is None:
                failed += 1
                continue
            for adv in advisories[:MAX_ADVISORIES_PER_PACKAGE]:
                fix = f" Fixed in {', '.join(adv['fixed_in'])}." if adv["fixed_in"] else ""
                scope = "" if exact else " (lowest version allowed by the declared range)"
                found = rules.issue(
                    "known-vulnerability",
                    adv["severity"],
                    path,
                    f"{name} {version}{scope}: {adv['id']} — {adv['summary']}.{fix}",
                    line,
                    snippet,
                )
                found.update(advisory=adv["id"], aliases=adv["aliases"], fixed_in=adv["fixed_in"])
                issues.append(found)
        return {
            "source": "osv.dev",
            "packages_checked": len(checked) - failed,
            "packages_not_checked": skipped + failed + len(targets) - len(checked),
            "issues": issues,
        }

    @server.tool()
    async def check_config(owner: str, repo: str, ref: str, token: str = "") -> dict:
        """Check repository hygiene: CI, tests, committed secrets files, Dockerfiles, workflows."""
        client = base.with_token(token)
        try:
            paths = await tree_paths(client, owner, repo, ref)
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
