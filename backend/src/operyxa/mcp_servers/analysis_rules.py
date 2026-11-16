"""Deterministic static checks. Each issue carries the exact path, line and snippet it is
based on, so an agent can cite it verbatim and the verifier can confirm the citation."""

import json
import re
import tomllib
from typing import Any

Issue = dict[str, Any]

MANIFEST_NAMES = ("requirements.txt", "pyproject.toml", "package.json")
LOCKFILES = {
    "python": ("poetry.lock", "uv.lock", "Pipfile.lock", "pdm.lock"),
    "node": ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb"),
}
_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9_.\-\[\]]+)\s*(.*)$")


def issue(
    rule: str,
    severity: str,
    path: str,
    message: str,
    line: int | None = None,
    snippet: str | None = None,
) -> Issue:
    return {
        "rule": rule,
        "severity_hint": severity,
        "path": path,
        "line": line,
        "snippet": snippet,
        "message": message,
    }


def is_manifest(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in MANIFEST_NAMES or (name.startswith("requirements") and name.endswith(".txt"))


def _line_of(text: str, needle: str) -> int | None:
    for number, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return number
    return None


def parse_requirements(path: str, text: str) -> tuple[list[dict], list[Issue]]:
    deps, issues = [], []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(" #")[0].strip()
        if not line or line.startswith(("#", "-")):
            continue
        match = _REQ_LINE.match(line)
        if not match:
            continue
        name, spec = match.group(1), match.group(2).strip()
        pinned = spec.startswith("==") or " @ " in line
        deps.append({"name": name, "spec": spec, "pinned": pinned, "line": number})
        if not pinned:
            issues.append(
                issue(
                    "unpinned-dependency",
                    "low",
                    path,
                    f"'{name}' is not pinned to an exact version",
                    number,
                    raw.strip(),
                )
            )
    return deps, issues


def parse_pyproject(path: str, text: str) -> tuple[list[dict], list[Issue]]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return [], [issue("invalid-manifest", "medium", path, f"pyproject.toml is invalid: {exc}")]
    deps = []
    for spec in data.get("project", {}).get("dependencies", []) or []:
        name = re.split(r"[<>=!~;\[ ]", spec, maxsplit=1)[0]
        deps.append(
            {
                "name": name,
                "spec": spec[len(name) :].strip(),
                "pinned": "==" in spec,
                "line": _line_of(text, spec),
            }
        )
    return deps, []


def parse_package_json(path: str, text: str) -> tuple[list[dict], list[Issue]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [], [issue("invalid-manifest", "medium", path, f"package.json is invalid: {exc}")]
    deps, issues = [], []
    for section in ("dependencies", "devDependencies"):
        for name, spec in (data.get(section) or {}).items():
            spec = str(spec)
            pinned = bool(re.match(r"^\d", spec))
            line = _line_of(text, f'"{name}"')
            deps.append(
                {
                    "name": name,
                    "spec": spec,
                    "pinned": pinned,
                    "line": line,
                    "dev": section == "devDependencies",
                }
            )
            if spec in ("*", "latest") or spec.startswith(("http:", "git+http:")):
                issues.append(
                    issue(
                        "unsafe-dependency-spec",
                        "medium",
                        path,
                        f"'{name}' uses the unsafe version spec '{spec}'",
                        line,
                        f'"{name}": "{spec}"',
                    )
                )
    return deps, issues


def parse_manifest(path: str, text: str) -> tuple[list[dict], list[Issue]]:
    name = path.rsplit("/", 1)[-1]
    if name == "pyproject.toml":
        return parse_pyproject(path, text)
    if name == "package.json":
        return parse_package_json(path, text)
    return parse_requirements(path, text)


def lockfile_issues(paths: set[str], manifests: list[str]) -> list[Issue]:
    names = {p.rsplit("/", 1)[-1] for p in paths}
    issues = []
    has_python = any(not m.endswith("package.json") for m in manifests)
    has_node = any(m.endswith("package.json") for m in manifests)
    if has_python and not names & set(LOCKFILES["python"]):
        only_requirements = all(m.endswith(".txt") for m in manifests if not m.endswith(".json"))
        if not only_requirements:
            issues.append(
                issue(
                    "missing-lockfile",
                    "medium",
                    "pyproject.toml",
                    "no Python lockfile found: builds are not reproducible",
                )
            )
    if has_node and not names & set(LOCKFILES["node"]):
        issues.append(
            issue(
                "missing-lockfile",
                "medium",
                "package.json",
                "no Node lockfile found: builds are not reproducible",
            )
        )
    return issues


def tree_issues(paths: set[str]) -> list[Issue]:
    issues = []
    for path in sorted(paths):
        name = path.rsplit("/", 1)[-1]
        if name == ".env" or (
            name.startswith(".env.")
            and not name.endswith((".example", ".sample", ".template", ".dist"))
        ):
            issues.append(
                issue(
                    "env-file-committed",
                    "high",
                    path,
                    "an environment file is committed; it may contain secrets",
                )
            )
        if name in ("id_rsa", "id_ed25519") or name.endswith((".pem", ".p12", ".pfx")):
            issues.append(
                issue(
                    "key-file-committed",
                    "high",
                    path,
                    "a private key or certificate file is committed",
                )
            )
    if not any(
        p.startswith(".github/workflows/")
        or p in (".gitlab-ci.yml", "Jenkinsfile")
        or p.startswith(".circleci/")
        for p in paths
    ):
        issues.append(issue("no-ci", "medium", ".", "no CI configuration was found"))
    if not any(
        re.search(r"(^|/)(tests?|__tests__|spec)/|(_test|\.test|\.spec)\.", p)
        or re.search(r"(^|/)test_[^/]+\.py$", p)
        for p in paths
    ):
        issues.append(issue("no-tests", "medium", ".", "no automated tests were found"))
    return issues


def dockerfile_issues(path: str, text: str) -> list[Issue]:
    issues, has_user = [], False
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        upper = line.upper()
        if upper.startswith("FROM "):
            image = line.split()[1] if len(line.split()) > 1 else ""
            if (
                image.lower() != "scratch"
                and "@sha256:" not in image
                and (":" not in image or image.endswith(":latest"))
            ):
                issues.append(
                    issue(
                        "docker-unpinned-base-image",
                        "medium",
                        path,
                        f"base image '{image}' is not pinned to a version",
                        number,
                        line,
                    )
                )
        if upper.startswith("USER ") and line.split()[1] not in ("root", "0"):
            has_user = True
    if not has_user:
        issues.append(
            issue(
                "docker-runs-as-root",
                "medium",
                path,
                "the container runs as root (no USER instruction)",
            )
        )
    return issues


def workflow_issues(path: str, text: str) -> list[Issue]:
    issues = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if re.search(r"\bpull_request_target\b", line) and not line.startswith("#"):
            issues.append(
                issue(
                    "workflow-pull-request-target",
                    "high",
                    path,
                    "pull_request_target runs with repository secrets on untrusted pull requests",
                    number,
                    line,
                )
            )
        match = re.search(r"uses:\s*([\w.\-]+/[\w.\-/]+)@(main|master|latest)\b", line)
        if match:
            issues.append(
                issue(
                    "workflow-action-on-branch",
                    "medium",
                    path,
                    f"action '{match.group(1)}' follows a moving branch instead of a release or SHA",
                    number,
                    line,
                )
            )
    return issues
