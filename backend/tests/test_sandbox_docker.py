"""F-07 against a real Docker daemon: the sandbox holds when the code under test is hostile.

Skipped when no Docker daemon is reachable. The image is python:3.12-slim with pytest baked in,
so that the install phase needs no network (the setting a hardened deployment would use).
"""

import asyncio
import os
import shutil
import subprocess

import pytest

from operyxa.mcp_servers.sandbox import DockerRuntime, SandboxLimits, SandboxRunner

from .conftest import make_tarball

IMAGE = "operyxa-sandbox-python-test:local"
CANARY = "ghp_canary_token_never_in_sandbox"


def _docker(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def _docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return _docker("version", "--format", "{{.Server.Version}}", timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(not _docker_ready(), reason="no Docker daemon")


@pytest.fixture(scope="module")
def image(tmp_path_factory) -> str:
    if _docker("image", "inspect", IMAGE).returncode == 0:
        return IMAGE
    # Built once. Proxy settings are build arguments: they never end up in the image.
    context = tmp_path_factory.mktemp("image")
    ca = "/root/.ccr/ca-bundle.crt"
    lines = ["FROM python:3.12-slim"]
    if os.path.exists(ca):
        shutil.copy(ca, context / "ca.crt")
        lines += ["COPY ca.crt /tmp/ca.crt", "ENV PIP_CERT=/tmp/ca.crt"]
    lines.append("RUN pip install -q pytest && rm -f /tmp/ca.crt")
    if os.path.exists(ca):
        lines.append("ENV PIP_CERT=")
    (context / "Dockerfile").write_text("\n".join(lines) + "\n")
    proxy = [f"--build-arg={k}" for k in ("HTTPS_PROXY", "https_proxy") if os.environ.get(k)]
    built = _docker("build", "--network", "host", *proxy, "-t", IMAGE, str(context), timeout=600)
    if built.returncode != 0:
        pytest.skip(f"cannot prepare the test image: {built.stderr[-300:]}")
    return IMAGE


HOSTILE_TESTS = f'''
import os, pathlib, socket

def test_no_outbound_network():
    s = socket.socket()
    s.settimeout(3)
    try:
        s.connect(("1.1.1.1", 53))
        connected = True
    except OSError:
        connected = False
    assert not connected

def test_no_dns():
    try:
        socket.getaddrinfo("github.com", 443)
        resolved = True
    except OSError:
        resolved = False
    assert not resolved

def test_runs_unprivileged():
    assert os.getuid() == 65534 and os.getgid() == 65534

def test_no_secret_in_environment():
    words = ("TOKEN", "SECRET", "KEY", "PASSWORD", "GITHUB", "DATABASE", "PROXY")
    public = {{"GPG_KEY"}}  # signing key id of the official python image
    assert [k for k in os.environ if any(w in k.upper() for w in words) and k not in public] == []

def test_secret_files_are_withheld():
    assert not pathlib.Path(".env").exists() and pathlib.Path(".env.example").exists()

def test_root_filesystem_is_read_only():
    try:
        pathlib.Path("/usr/local/evil").write_text("x")
        wrote = True
    except OSError:
        wrote = False
    assert not wrote

def test_token_is_nowhere_in_the_workspace():
    for path in pathlib.Path(".").rglob("*"):
        if ".operyxa" in path.parts or not path.is_file():
            continue
        assert "{CANARY[:10]}" + "{CANARY[10:]}" not in path.read_text(errors="ignore")

def test_deliberately_failing():
    assert 1 + 1 == 3
'''


def sandbox(image: str, **limits) -> SandboxRunner:
    defaults = {"timeout_seconds": 120, "install_network": False, "memory_mb": 512}
    return SandboxRunner(
        DockerRuntime(SandboxLimits(**{**defaults, **limits})), images={"python-pytest": image}
    )


def leftovers() -> tuple[str, str]:
    containers = _docker("ps", "-aq", "--filter", "label=operyxa.sandbox=1").stdout.strip()
    volumes = _docker("volume", "ls", "-q", "--filter", "label=operyxa.sandbox=1").stdout.strip()
    return containers, volumes


async def test_hostile_test_suite_is_contained(image):  # T-06 + sandbox escape attempts
    archive = make_tarball(
        {
            "pytest.ini": "[pytest]\n",
            ".env": f"GITHUB_TOKEN={CANARY}\n",
            ".env.example": "GITHUB_TOKEN=\n",
            "tests/test_hostile.py": HOSTILE_TESTS,
        }
    )
    result = await sandbox(image).run("python-pytest", archive)
    assert result["status"] == "failed", result
    assert result["counts"] == {"failed": 1, "passed": 7}, result["output_tail"]
    assert [t["test"] for t in result["failed_tests"]] == [
        "tests/test_hostile.py::test_deliberately_failing"
    ]
    assert result["install"]["exit_code"] == 0 and ".env" in result["withheld_files"]
    assert leftovers() == ("", "")


async def test_runaway_tests_are_killed_on_timeout(image):
    archive = make_tarball(
        {
            "requirements.txt": "",
            "tests/test_slow.py": "import time\n\ndef test_forever():\n    time.sleep(600)\n",
        }
    )
    started = asyncio.get_running_loop().time()
    result = await sandbox(image, timeout_seconds=8).run("python-pytest", archive)
    assert result["status"] == "timeout" and result["exit_code"] is None
    assert asyncio.get_running_loop().time() - started < 60
    assert leftovers() == ("", "")


async def test_memory_limit_is_enforced(image):
    archive = make_tarball(
        {
            "requirements.txt": "",
            "tests/test_memory.py": "def test_eat():\n    blob = bytearray(2 * 1024**3)\n",
        }
    )
    result = await sandbox(image, memory_mb=256).run("python-pytest", archive)
    assert result["status"] == "failed" and result["exit_code"] != 0
    assert leftovers() == ("", "")


async def test_full_run_with_approval_on_real_docker(env, settings, github, image):
    """API option -> plan with a testing step -> approval -> real sandbox -> verified finding."""
    from operyxa.bootstrap import build_providers
    from operyxa.db.models import Finding
    from operyxa.gateway.gateway import ToolGateway
    from operyxa.worker.runner import execute_run

    from .conftest import fake_osv, make_run
    from .test_test_runner import decide_when_asked, rows

    settings.test_runner_enabled = True
    settings.approval_poll_seconds = 0.05
    settings.max_run_seconds = 300
    # No third-party dependency: installation works offline with the baked-in image.
    github.files = {
        **{k: v for k, v in github.files.items() if k != "requirements.txt"},
        "pytest.ini": "[pytest]\n",
        "tests/test_price.py": "def test_total():\n    assert sum([2, 3]) == 6\n",
    }
    env.gateway = ToolGateway(
        settings,
        env.store,
        build_providers(settings, github.client(), fake_osv(), sandbox(image)),
    )
    await env.gateway.discover()
    run = await make_run(env)
    await env.store.update_run(run.id, options={"run_tests": True})
    decider = asyncio.create_task(decide_when_asked(env, run.id, "approved"))
    await asyncio.wait_for(execute_run(run.id, env), 240)
    await decider
    assert (await env.store.get_run(run.id)).status == "completed"
    failing = next(f for f in await rows(env, Finding, run.id) if f.agent == "testing")
    assert failing.status == "verified" and "tests/test_price.py::test_total" in failing.title
    assert failing.evidence[0]["path"] == "tests/test_price.py"
    assert leftovers() == ("", "")
