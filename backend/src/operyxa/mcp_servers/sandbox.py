"""Isolated execution of a repository's test suite.

The code under test is untrusted: it comes from the analysed repository and may be hostile.
Everything here assumes that, and none of the protections relies on the model or the prompt:

- only server-defined profiles run (fixed image, fixed commands); the model picks a profile name,
  never a command line;
- the repository is fetched by the server at the pinned commit, outside the container: the GitHub
  token never enters it, and files that usually hold secrets (.env, keys) are removed;
- the tests run in a throw-away container without network, as an unprivileged user, with no
  Linux capabilities, a read-only root filesystem and CPU, memory, process and time limits;
- dependency installation is a separate phase, run before the tests in its own container. It is
  the only phase that may have network access (TEST_RUNNER_INSTALL_NETWORK), and it never sees
  secrets either.

The container runtime is Docker (CLI). A named volume carries the workspace between the two
phases, so the runner also works against a remote Docker daemon.
"""

import asyncio
import contextlib
import io
import re
import secrets
import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from operyxa.gateway.catalog import is_sensitive_path

SANDBOX_USER = "65534:65534"  # nobody
OUTPUT_TAIL_CHARS = 12_000
MAX_CAPTURE_BYTES = 1_000_000
MAX_FAILED_TESTS = 30
MAX_WORKSPACE_FILES = 20_000


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    image: str
    detect: tuple[str, ...]  # at least one of these files must exist at the repository root
    install: str
    test: str
    parser: str  # "pytest" | "jest" | "exit_code"


PROFILES: dict[str, Profile] = {
    p.name: p
    for p in (
        Profile(
            name="python-pytest",
            description="Python project: install it in a virtualenv, then run pytest.",
            image="python:3.12-slim",
            detect=("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pytest.ini"),
            install=(
                "set -e\n"
                # System site-packages: an image with dependencies baked in installs nothing.
                "python -m venv --system-site-packages /work/.operyxa/venv\n"
                ". /work/.operyxa/venv/bin/activate\n"
                "export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1\n"
                "pip install -q pytest\n"
                "for f in requirements.txt requirements-dev.txt requirements-test.txt; do\n"
                '  if [ -f "$f" ]; then pip install -q -r "$f"; fi\n'
                "done\n"
                "if [ -f pyproject.toml ] || [ -f setup.py ]; then\n"
                "  pip install -q -e '.[test]' 2>/dev/null || pip install -q -e '.[dev]' "
                "2>/dev/null || pip install -q -e .\n"
                "fi\n"
            ),
            test=(
                ". /work/.operyxa/venv/bin/activate\n"
                "python -m pytest -q -rfE --color=no -p no:cacheprovider\n"
            ),
            parser="pytest",
        ),
        Profile(
            name="node-npm-test",
            description="Node.js project: npm ci with install scripts disabled, then npm test.",
            image="node:22-slim",
            detect=("package.json",),
            install=(
                "set -e\n"
                "if [ -f package-lock.json ]; then npm ci --ignore-scripts --no-audit --no-fund; "
                "else npm install --ignore-scripts --no-audit --no-fund; fi\n"
            ),
            test="npm test --silent\n",
            parser="jest",
        ),
    )
}


class SandboxError(Exception):
    """`code` is stable and machine readable (mapped by the MCP server to a tool error)."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class SandboxLimits:
    timeout_seconds: int = 300
    install_timeout_seconds: int = 300
    memory_mb: int = 1024
    cpus: float = 1.0
    pids: int = 256
    tmpfs_mb: int = 512
    install_network: bool = True
    runtime: str = ""  # e.g. "runsc" (gVisor) for a stronger kernel boundary


@dataclass
class PhaseResult:
    exit_code: int | None  # None = killed on timeout
    output: str
    duration_seconds: float
    timed_out: bool = False


@dataclass
class SuiteRun:
    profile: str
    status: str  # passed | failed | no_tests | install_failed | timeout
    exit_code: int | None
    duration_seconds: float
    counts: dict[str, int] = field(default_factory=dict)
    failed_tests: list[dict[str, str]] = field(default_factory=list)
    output: str = ""
    install: dict[str, Any] | None = None

    def as_dict(self, limits: SandboxLimits) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "status": self.status,
            "exit_code": self.exit_code,
            "duration_seconds": round(self.duration_seconds, 1),
            "counts": self.counts,
            "failed_tests": self.failed_tests,
            "output_tail": self.output,
            "install": self.install,
            "sandbox": {
                "network": "none",
                "install_network": "enabled" if limits.install_network else "none",
                "user": SANDBOX_USER,
                "memory_mb": limits.memory_mb,
                "cpus": limits.cpus,
                "timeout_seconds": limits.timeout_seconds,
                "secrets": "none",
            },
        }


class ContainerRuntime(Protocol):
    """What the sandbox needs from a container engine. Docker is the implementation."""

    limits: SandboxLimits

    async def check(self) -> None: ...

    async def ensure_image(self, image: str) -> None: ...

    async def create_workspace(self, workspace: Path, image: str) -> str: ...

    async def remove_workspace(self, volume: str) -> None: ...

    async def run_phase(
        self, volume: str, profile: Profile, script: str, *, network: bool, seconds: int
    ) -> PhaseResult: ...


# --------------------------------------------------------------------------------------------
# Workspace preparation (host side, before anything runs)


def extract_archive(data: bytes, dest: Path) -> list[str]:
    """Extract a GitHub tarball (one top-level folder) into `dest`. Returns removed paths.

    Only regular files and directories are kept: links, devices and paths escaping `dest` are
    dropped. Files that usually hold secrets are removed before the container ever sees them.
    """
    removed: list[str] = []
    count = 0
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except tarfile.TarError as exc:
        raise SandboxError("INVALID_ARCHIVE", "the repository archive is unreadable") from exc
    with archive:
        for member in archive:
            parts = member.name.split("/", 1)
            if len(parts) < 2 or not parts[1]:
                continue  # the top-level "<owner>-<repo>-<sha>/" folder
            rel = parts[1].rstrip("/")
            if rel.startswith("/") or any(p in ("", "..") for p in rel.split("/")):
                removed.append(rel)
                continue
            if not (member.isfile() or member.isdir()):
                removed.append(rel)  # symlinks, hard links, devices
                continue
            if member.isfile() and is_sensitive_path(rel):
                removed.append(rel)
                continue
            count += 1
            if count > MAX_WORKSPACE_FILES:
                raise SandboxError("TOO_LARGE", "the repository has too many files")
            target = dest / rel
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                continue
            with source, open(target, "wb") as out:
                shutil.copyfileobj(source, out)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
    return removed


def applicable_profiles(root_files: set[str]) -> list[str]:
    return [p.name for p in PROFILES.values() if any(f in root_files for f in p.detect)]


# --------------------------------------------------------------------------------------------
# Result parsing

# "=== 1 failed, 3 passed in 0.12s ===" (default) or "1 failed, 3 passed in 0.12s" (-q)
_PYTEST_SUMMARY = re.compile(
    r"^(?:=+ )?(\d+ (?:passed|failed|errors?|skipped|xfailed|xpassed|deselected|warnings?)"
    r".*?) in [\d.]+s(?: \([^)]*\))?(?: =+)?$",
    re.M,
)
_PYTEST_COUNT = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected)")
_PYTEST_FAILED = re.compile(r"^(FAILED|ERROR) (\S+?)(?: - (.*))?$", re.M)
_JEST_TESTS = re.compile(r"^Tests:\s+(.+)$", re.M)
_JEST_COUNT = re.compile(r"(\d+) (passed|failed|skipped|todo|total)")
_JEST_FAILED = re.compile(r"^\s*● (.+?) › (.+)$", re.M)
_JEST_FILE = re.compile(r"^FAIL (\S+)", re.M)


def parse_output(parser: str, output: str) -> tuple[dict[str, int], list[dict[str, str]]]:
    counts: dict[str, int] = {}
    failed: list[dict[str, str]] = []
    if parser == "pytest":
        summaries = _PYTEST_SUMMARY.findall(output)
        if summaries:
            for number, kind in _PYTEST_COUNT.findall(summaries[-1]):
                counts["errors" if kind.startswith("error") else kind] = int(number)
        for kind, nodeid, reason in _PYTEST_FAILED.findall(output):
            failed.append(
                {
                    "outcome": kind.lower(),
                    "test": nodeid,
                    "path": nodeid.split("::", 1)[0],
                    "reason": (reason or "").strip()[:300],
                    "report_line": f"{kind} {nodeid}",
                }
            )
    elif parser == "jest":
        lines = _JEST_TESTS.findall(output)
        if lines:
            for number, kind in _JEST_COUNT.findall(lines[-1]):
                counts[kind] = int(number)
        files = _JEST_FILE.findall(output)
        for suite, name in _JEST_FAILED.findall(output):
            failed.append(
                {
                    "outcome": "failed",
                    "test": f"{suite} › {name}".strip(),
                    "path": files[0] if len(files) == 1 else "",
                    "reason": "",
                    "report_line": f"● {suite} › {name}".strip(),
                }
            )
    return counts, failed[:MAX_FAILED_TESTS]


def _status(parser: str, exit_code: int | None, counts: dict[str, int]) -> str:
    if exit_code is None:
        return "timeout"
    if parser == "pytest" and exit_code == 5:
        return "no_tests"
    return "passed" if exit_code == 0 else "failed"


def tail(text: str, limit: int = OUTPUT_TAIL_CHARS) -> str:
    text = text.replace("\r\n", "\n")
    return text if len(text) <= limit else "[earlier output truncated]\n" + text[-limit:]


# --------------------------------------------------------------------------------------------
# Docker runtime


class DockerRuntime:
    """Runs each phase in a fresh container via the Docker CLI.

    The workspace travels in a named volume (not a bind mount): it works with a remote daemon,
    and the container never sees a host path.
    """

    def __init__(self, limits: SandboxLimits, docker: str = "docker"):
        self.limits = limits
        self.docker = docker

    async def _cli(self, *args: str, seconds: float = 120) -> tuple[int, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.docker,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError as exc:
            raise SandboxError("SANDBOX_UNAVAILABLE", "the docker CLI is not installed") from exc
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), seconds)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise SandboxError("SANDBOX_UNAVAILABLE", f"docker {args[0]} did not answer") from None
        return proc.returncode or 0, out.decode(errors="replace")

    async def check(self) -> None:
        code, out = await self._cli("version", "--format", "{{.Server.Version}}", seconds=15)
        if code != 0:
            raise SandboxError("SANDBOX_UNAVAILABLE", "the Docker daemon is not reachable")

    async def ensure_image(self, image: str) -> None:
        code, _ = await self._cli("image", "inspect", image, seconds=30)
        if code != 0:
            code, out = await self._cli("pull", "--quiet", image, seconds=600)
            if code != 0:
                raise SandboxError("SANDBOX_UNAVAILABLE", f"cannot pull image {image}")

    def _base_args(self, name: str, volume: str, network: bool) -> list[str]:
        lim = self.limits
        args = [
            "run",
            "--rm",
            "--name",
            name,
            "--label",
            "operyxa.sandbox=1",
            "--network",
            "bridge" if network else "none",
            "--user",
            SANDBOX_USER,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,exec,nosuid,nodev,size={lim.tmpfs_mb}m",
            "--memory",
            f"{lim.memory_mb}m",
            "--memory-swap",
            f"{lim.memory_mb}m",
            "--cpus",
            str(lim.cpus),
            "--pids-limit",
            str(lim.pids),
            "--ulimit",
            "nofile=1024:1024",
            "--mount",
            f"type=volume,source={volume},target=/work",
            "--workdir",
            "/work",
            # The only environment the code under test gets. Docker passes nothing from the host.
            "--env",
            "HOME=/tmp",
            "--env",
            "CI=true",
            "--env",
            "PIP_CACHE_DIR=/tmp/pip-cache",
            "--env",
            "npm_config_cache=/tmp/npm-cache",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
        ]
        if lim.runtime:
            args += ["--runtime", lim.runtime]
        return args

    async def create_workspace(self, workspace: Path, image: str) -> str:
        """Copy the prepared files into a fresh volume owned by the sandbox user."""
        volume = "operyxa-ws-" + secrets.token_hex(6)
        code, out = await self._cli("volume", "create", "--label", "operyxa.sandbox=1", volume)
        if code != 0:
            raise SandboxError("SANDBOX_UNAVAILABLE", "cannot create a workspace volume")
        helper = "operyxa-cp-" + secrets.token_hex(6)
        try:
            code, out = await self._cli(
                "create",
                "--name",
                helper,
                "--network",
                "none",
                "--mount",
                f"type=volume,source={volume},target=/work",
                image,
                "chown",
                "-R",
                SANDBOX_USER,
                "/work",
            )
            if code != 0:
                raise SandboxError("SANDBOX_UNAVAILABLE", "cannot prepare the workspace")
            code, out = await self._cli("cp", f"{workspace}/.", f"{helper}:/work", seconds=300)
            if code != 0:
                raise SandboxError("SANDBOX_UNAVAILABLE", "cannot copy the workspace")
            code, out = await self._cli("start", "--attach", helper, seconds=300)
            if code != 0:
                raise SandboxError("SANDBOX_UNAVAILABLE", "cannot set workspace ownership")
        except BaseException:
            await self.remove_workspace(volume)
            raise
        finally:
            await self._cli("rm", "--force", helper)
        return volume

    async def remove_workspace(self, volume: str) -> None:
        with contextlib.suppress(SandboxError):
            await self._cli("volume", "rm", "--force", volume)

    async def run_phase(
        self, volume: str, profile: Profile, script: str, *, network: bool, seconds: int
    ) -> PhaseResult:
        name = "operyxa-sbx-" + secrets.token_hex(6)
        args = [*self._base_args(name, volume, network), profile.image, "sh", "-c", script]
        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            self.docker, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        buffer = bytearray()

        async def drain() -> None:
            # Keep only the end: a test suite printing gigabytes must not exhaust our memory.
            assert proc.stdout is not None
            while chunk := await proc.stdout.read(65536):
                buffer.extend(chunk)
                if len(buffer) > MAX_CAPTURE_BYTES:
                    del buffer[: len(buffer) - MAX_CAPTURE_BYTES]
            await proc.wait()

        timed_out = False
        reader = asyncio.ensure_future(drain())
        try:
            await asyncio.wait_for(asyncio.shield(reader), seconds)
        except TimeoutError:
            timed_out = True
            await self._cli("kill", name, seconds=30)
            await reader
        except BaseException:  # cancelled run: never leave a container behind
            await self._cli("kill", name, seconds=30)
            reader.cancel()
            raise
        code = None if timed_out else proc.returncode
        return PhaseResult(
            code, buffer.decode(errors="replace"), time.monotonic() - started, timed_out
        )


# --------------------------------------------------------------------------------------------
# Orchestration


class SandboxRunner:
    def __init__(
        self,
        runtime: ContainerRuntime,
        max_concurrent: int = 1,
        images: dict[str, str] | None = None,
    ):
        """`images` replaces a profile's image, e.g. with one that has dependencies baked in
        (then installation needs no network) or that comes from a private registry."""
        self.runtime = runtime
        self.limits = runtime.limits
        self.profiles = {
            name: replace(p, image=(images or {}).get(name, p.image))
            for name, p in PROFILES.items()
        }
        self._slots = asyncio.Semaphore(max(1, max_concurrent))

    async def run(self, profile_name: str, archive: bytes) -> dict[str, Any]:
        profile = self.profiles.get(profile_name)
        if profile is None:
            raise SandboxError(
                "PROFILE_UNKNOWN", f"unknown profile; available: {', '.join(PROFILES)}"
            )
        async with self._slots:
            await self.runtime.check()
            await self.runtime.ensure_image(profile.image)
            with tempfile.TemporaryDirectory(prefix="operyxa-ws-") as tmp:
                workspace = Path(tmp)
                removed = await asyncio.to_thread(extract_archive, archive, workspace)
                root_files = await asyncio.to_thread(lambda: {p.name for p in workspace.iterdir()})
                if profile.name not in applicable_profiles(root_files):
                    raise SandboxError(
                        "PROFILE_NOT_APPLICABLE",
                        f"{profile.name} needs one of {', '.join(profile.detect)} at the root",
                    )
                volume = await self.runtime.create_workspace(workspace, profile.image)
            try:
                result = await self._run_phases(profile, volume)
            finally:
                await self.runtime.remove_workspace(volume)
        out = result.as_dict(self.limits)
        out["withheld_files"] = removed[:50]
        return out

    async def _run_phases(self, profile: Profile, volume: str) -> SuiteRun:
        lim = self.limits
        install = await self.runtime.run_phase(
            volume,
            profile,
            profile.install,
            network=lim.install_network,
            seconds=lim.install_timeout_seconds,
        )
        install_info = {
            "exit_code": install.exit_code,
            "duration_seconds": round(install.duration_seconds, 1),
            "network": "enabled" if lim.install_network else "none",
        }
        if install.exit_code != 0:
            install_info["output_tail"] = tail(install.output, 4000)
            return SuiteRun(
                profile.name,
                "timeout" if install.timed_out else "install_failed",
                install.exit_code,
                install.duration_seconds,
                install=install_info,
            )
        test = await self.runtime.run_phase(
            volume, profile, profile.test, network=False, seconds=lim.timeout_seconds
        )
        counts, failed = parse_output(profile.parser, test.output)
        return SuiteRun(
            profile.name,
            _status(profile.parser, test.exit_code, counts),
            test.exit_code,
            test.duration_seconds,
            counts,
            failed,
            tail(test.output),
            install_info,
        )
