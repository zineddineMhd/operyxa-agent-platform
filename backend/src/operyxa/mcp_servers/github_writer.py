"""The only code that writes to GitHub (F-12). No agent and no MCP tool can reach it.

It is called by the API after a person confirmed a pull request proposal, with a token that has
write access to that one repository. It creates a branch from the analysed commit, one commit
with the proposed files, and a draft pull request. It never pushes to an existing branch.
"""

from typing import Any

import httpx

from operyxa.mcp_servers.github_client import (
    GitHubError,
    _raise_for_status,
    check_path,
    check_ref,
    check_repo,
)


class GitHubWriter:
    def __init__(self, http: httpx.AsyncClient, token: str):
        if not token:
            raise GitHubError("ACCESS_DENIED", "a write token is required")
        self._http = http
        self._headers = {"Authorization": f"Bearer {token}"}

    @classmethod
    def create(cls, api_url: str, token: str, timeout: float = 30.0) -> "GitHubWriter":
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "repo-analysis-agent",
        }
        return cls(httpx.AsyncClient(base_url=api_url, headers=headers, timeout=timeout), token)

    async def _send(self, method: str, url: str, json: dict | None = None) -> Any:
        try:
            resp = await self._http.request(method, url, json=json, headers=self._headers)
        except httpx.HTTPError as exc:
            raise GitHubError("UPSTREAM_UNAVAILABLE", type(exc).__name__) from exc
        if resp.status_code == 422:
            message = ""
            try:
                message = str(resp.json().get("message", ""))[:200]
            except ValueError:
                pass
            raise GitHubError("UNPROCESSABLE", message or "GitHub refused the request")
        _raise_for_status(resp.status_code)
        return resp.json()

    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        *,
        base_sha: str,
        branch: str,
        title: str,
        body: str,
        files: list[dict[str, str]],
    ) -> dict[str, Any]:
        check_repo(owner, repo)
        check_ref(base_sha)
        check_ref(branch)
        if not files:
            raise GitHubError("INVALID_ARGUMENT", "nothing to commit")
        prefix = f"/repos/{owner}/{repo}"
        info = await self._send("GET", prefix)
        base_commit = await self._send("GET", f"{prefix}/git/commits/{base_sha}")
        tree = await self._send(
            "POST",
            f"{prefix}/git/trees",
            {
                "base_tree": base_commit["tree"]["sha"],
                "tree": [
                    {
                        "path": check_path(f["path"]),
                        "mode": "100644",
                        "type": "blob",
                        "content": f["content"],
                    }
                    for f in files
                ],
            },
        )
        commit = await self._send(
            "POST",
            f"{prefix}/git/commits",
            {"message": title, "tree": tree["sha"], "parents": [base_sha]},
        )
        # Creating the ref fails (422) if the branch exists: an existing branch is never moved.
        await self._send(
            "POST", f"{prefix}/git/refs", {"ref": f"refs/heads/{branch}", "sha": commit["sha"]}
        )
        pr = await self._send(
            "POST",
            f"{prefix}/pulls",
            {
                "title": title,
                "head": branch,
                "base": info["default_branch"],
                "body": body,
                "draft": True,
            },
        )
        return {"number": pr["number"], "url": pr["html_url"], "commit_sha": commit["sha"]}
