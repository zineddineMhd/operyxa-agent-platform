"""Minimal read-only GitHub REST client shared by the MCP servers.

Only GET requests exist here: the read-only guarantee of the MVP is structural.
"""

import re
from typing import Any
from urllib.parse import urlsplit

import httpx

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_SAFE_REF = re.compile(r"^[A-Za-z0-9_./-]{1,200}$")
MAX_FILE_BYTES = 400_000
# Hosts a tarball redirect may point to. The token is never sent to them: the redirect URL is
# already signed by GitHub.
ARCHIVE_HOSTS = ("codeload.github.com",)


class GitHubError(Exception):
    """`code` is stable and machine readable; the gateway maps it to retry behaviour."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def check_repo(owner: str, repo: str) -> None:
    if not _SAFE_NAME.match(owner) or not _SAFE_NAME.match(repo):
        raise GitHubError("INVALID_ARGUMENT", "invalid repository owner or name")


def check_ref(ref: str) -> None:
    if not _SAFE_REF.match(ref) or ".." in ref:
        raise GitHubError("INVALID_ARGUMENT", "invalid git ref")


def check_path(path: str) -> str:
    path = path.strip()
    if not path or path.startswith("/") or "\\" in path or "\x00" in path:
        raise GitHubError("INVALID_ARGUMENT", "path must be relative to the repository root")
    if any(part in ("..", "") for part in path.split("/")):
        raise GitHubError("INVALID_ARGUMENT", "path must not contain '..' or empty segments")
    return path


class GitHubClient:
    def __init__(self, http: httpx.AsyncClient, token: str = ""):
        self._http = http
        self._token = token

    @classmethod
    def create(cls, api_url: str, token: str = "", timeout: float = 20.0) -> "GitHubClient":
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "repo-analysis-agent",
        }
        # follow_redirects stays off: a redirect must never carry the token to another host.
        return cls(httpx.AsyncClient(base_url=api_url, headers=headers, timeout=timeout), token)

    def with_token(self, token: str) -> "GitHubClient":
        """Same connection pool, another identity: the token of the project being analysed."""
        return GitHubClient(self._http, token) if token else self

    async def _get(self, url: str, *, params: dict | None = None, raw: bool = False) -> Any:
        headers = {"Accept": "application/vnd.github.raw+json"} if raw else {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            resp = await self._http.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise GitHubError("UPSTREAM_UNAVAILABLE", type(exc).__name__) from exc
        if resp.status_code == 404:
            raise GitHubError("NOT_FOUND", "resource not found (or not accessible)")
        if resp.status_code in (403, 429) and resp.headers.get("x-ratelimit-remaining") == "0":
            raise GitHubError("RATE_LIMITED", "GitHub API rate limit reached")
        if resp.status_code in (401, 403):
            raise GitHubError("ACCESS_DENIED", "GitHub refused access to this resource")
        if resp.status_code >= 500:
            raise GitHubError("UPSTREAM_UNAVAILABLE", f"GitHub returned {resp.status_code}")
        if resp.status_code >= 400:
            raise GitHubError("UPSTREAM_ERROR", f"GitHub returned {resp.status_code}")
        return resp.text if raw else resp.json()

    async def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        check_repo(owner, repo)
        data = await self._get(f"/repos/{owner}/{repo}")
        head = await self._get(f"/repos/{owner}/{repo}/commits/{data['default_branch']}")
        return {
            "full_name": data["full_name"],
            "description": data.get("description"),
            "default_branch": data["default_branch"],
            "language": data.get("language"),
            "private": data.get("private", False),
            "head_sha": head["sha"],
        }

    async def get_tree(self, owner: str, repo: str, ref: str) -> list[dict[str, Any]]:
        check_repo(owner, repo)
        check_ref(ref)
        data = await self._get(f"/repos/{owner}/{repo}/git/trees/{ref}", params={"recursive": "1"})
        return [
            {
                "path": e["path"],
                "type": "dir" if e["type"] == "tree" else "file",
                "size": e.get("size"),
            }
            for e in data.get("tree", [])
            if e["type"] in ("blob", "tree")
        ]

    async def get_file(self, owner: str, repo: str, ref: str, path: str) -> str:
        check_repo(owner, repo)
        check_ref(ref)
        path = check_path(path)
        text = await self._get(
            f"/repos/{owner}/{repo}/contents/{path}", params={"ref": ref}, raw=True
        )
        if len(text) > MAX_FILE_BYTES:
            raise GitHubError("TOO_LARGE", f"file is larger than {MAX_FILE_BYTES} bytes")
        if "\x00" in text:
            raise GitHubError("BINARY_FILE", "binary files are not readable")
        return text

    async def list_commits(self, owner: str, repo: str, ref: str, limit: int) -> list[dict]:
        check_repo(owner, repo)
        check_ref(ref)
        data = await self._get(
            f"/repos/{owner}/{repo}/commits", params={"sha": ref, "per_page": limit}
        )
        return [
            {
                "sha": c["sha"],
                "message": (c["commit"]["message"] or "")[:500],
                "author": (c["commit"].get("author") or {}).get("name"),
                "date": (c["commit"].get("author") or {}).get("date"),
            }
            for c in data
        ]

    async def get_commit(self, owner: str, repo: str, sha: str) -> dict[str, Any]:
        check_repo(owner, repo)
        check_ref(sha)
        c = await self._get(f"/repos/{owner}/{repo}/commits/{sha}")
        return {
            "sha": c["sha"],
            "message": (c["commit"]["message"] or "")[:1000],
            "stats": c.get("stats"),
            "files": [
                {
                    "path": f["filename"],
                    "status": f["status"],
                    "additions": f["additions"],
                    "deletions": f["deletions"],
                    "patch": (f.get("patch") or "")[:4000],
                }
                for f in c.get("files", [])[:40]
            ],
        }

    async def get_tarball(self, owner: str, repo: str, ref: str, max_bytes: int) -> bytes:
        """Archive of one commit, for the test sandbox. GitHub answers with a redirect to a
        signed download URL: it is followed by hand, only to a known host, without the token."""
        check_repo(owner, repo)
        check_ref(ref)
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        url = f"/repos/{owner}/{repo}/tarball/{ref}"
        try:
            for hop in range(2):
                async with self._http.stream("GET", url, headers=headers) as resp:
                    if resp.status_code in (301, 302, 307, 308) and hop == 0:
                        url = resp.headers.get("location", "")
                        parts = urlsplit(url)
                        if parts.scheme != "https" or parts.hostname not in ARCHIVE_HOSTS:
                            raise GitHubError("UPSTREAM_ERROR", "unexpected archive location")
                        headers = {}  # the signed URL needs no token, and must not see it
                        continue
                    _raise_for_status(resp.status_code)
                    data = bytearray()
                    async for chunk in resp.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > max_bytes:
                            raise GitHubError(
                                "TOO_LARGE", f"repository archive is larger than {max_bytes} bytes"
                            )
                    return bytes(data)
        except httpx.HTTPError as exc:
            raise GitHubError("UPSTREAM_UNAVAILABLE", type(exc).__name__) from exc
        raise GitHubError("UPSTREAM_ERROR", "too many redirects")


def _raise_for_status(status: int) -> None:
    if status == 404:
        raise GitHubError("NOT_FOUND", "resource not found (or not accessible)")
    if status in (401, 403, 429):
        raise GitHubError("ACCESS_DENIED", "GitHub refused access to this resource")
    if status >= 500:
        raise GitHubError("UPSTREAM_UNAVAILABLE", f"GitHub returned {status}")
    if status >= 300:
        raise GitHubError("UPSTREAM_ERROR", f"GitHub returned {status}")
