"""Known-vulnerability lookup against OSV (https://osv.dev), the open advisory database.

Only package names and versions leave the platform — never source code. The destination is a
fixed, configured host: nothing the model or the repository says can change where we connect.
"""

import asyncio
import re
from typing import Any

import httpx

ECOSYSTEMS = {"requirements": "PyPI", "pyproject.toml": "PyPI", "package.json": "npm"}
SEVERITY = {"CRITICAL": "critical", "HIGH": "high", "MODERATE": "medium", "LOW": "low"}
MAX_PACKAGES = 40
MAX_ADVISORIES_PER_PACKAGE = 5
_VERSION = re.compile(r"\d+(?:\.\d+)*(?:[a-zA-Z0-9.+-]*)")


def ecosystem_of(manifest_path: str) -> str | None:
    name = manifest_path.rsplit("/", 1)[-1]
    if name.startswith("requirements") and name.endswith(".txt"):
        return "PyPI"
    return ECOSYSTEMS.get(name)


def resolved_version(spec: str) -> tuple[str | None, bool]:
    """(version, exact). `==1.2` and `1.2` are exact; `^1.2`, `~1.2`, `>=1.2` give the lowest
    version the range allows, which is what an old install may still be running."""
    spec = spec.strip()
    match = _VERSION.search(spec)
    if not match:
        return None, False
    exact = spec.startswith("==") or spec[:1].isdigit()
    return match.group(0), exact


def _fixed_versions(vuln: dict[str, Any], package: str) -> list[str]:
    fixed = []
    for affected in vuln.get("affected", []):
        if affected.get("package", {}).get("name", "").lower() != package.lower():
            continue
        for rng in affected.get("ranges", []):
            fixed += [e["fixed"] for e in rng.get("events", []) if "fixed" in e]
    return sorted(set(fixed))


class OSVClient:
    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    @classmethod
    def create(cls, api_url: str, timeout: float = 15.0) -> "OSVClient":
        return cls(httpx.AsyncClient(base_url=api_url, timeout=timeout))

    async def query(self, ecosystem: str, name: str, version: str) -> list[dict[str, Any]]:
        response = await self._http.post(
            "/v1/query",
            json={"package": {"name": name, "ecosystem": ecosystem}, "version": version},
        )
        response.raise_for_status()
        out = []
        for vuln in response.json().get("vulns", []):
            severity = str((vuln.get("database_specific") or {}).get("severity", "")).upper()
            out.append(
                {
                    "id": vuln["id"],
                    "aliases": [a for a in vuln.get("aliases", []) if a.startswith("CVE-")][:3],
                    "summary": (vuln.get("summary") or vuln.get("details") or "")[:200],
                    "severity": SEVERITY.get(severity, "medium"),
                    "fixed_in": _fixed_versions(vuln, name)[:4],
                }
            )
        order = ["critical", "high", "medium", "low"]
        out.sort(key=lambda v: order.index(v["severity"]))
        return out

    async def query_many(self, packages: list[tuple[str, str, str]]) -> list[list[dict] | None]:
        """One lookup per package, a few at a time. `None` marks a lookup that failed."""
        gate = asyncio.Semaphore(6)

        async def one(package: tuple[str, str, str]) -> list[dict] | None:
            async with gate:
                try:
                    return await self.query(*package)
                except (httpx.HTTPError, ValueError, KeyError):
                    return None

        return list(await asyncio.gather(*(one(p) for p in packages)))
