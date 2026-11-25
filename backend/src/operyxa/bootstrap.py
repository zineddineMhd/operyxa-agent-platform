"""Wiring shared by the worker and the tests."""

from contextlib import asynccontextmanager

from langgraph.checkpoint.memory import InMemorySaver

from operyxa.config import Settings
from operyxa.gateway.providers import MCPProvider, ToolProvider
from operyxa.mcp_servers.analysis_server import build_analysis_server
from operyxa.mcp_servers.github_client import GitHubClient
from operyxa.mcp_servers.github_server import build_github_server
from operyxa.mcp_servers.osv import OSVClient
from operyxa.mcp_servers.sandbox import DockerRuntime, SandboxLimits, SandboxRunner
from operyxa.mcp_servers.test_runner_server import build_test_runner_server


def build_sandbox(settings: Settings) -> SandboxRunner:
    limits = SandboxLimits(
        timeout_seconds=settings.test_runner_timeout_seconds,
        install_timeout_seconds=settings.test_runner_install_timeout_seconds,
        memory_mb=settings.test_runner_memory_mb,
        cpus=settings.test_runner_cpus,
        install_network=settings.test_runner_install_network,
        runtime=settings.test_runner_docker_runtime,
    )
    return SandboxRunner(
        DockerRuntime(limits), settings.test_runner_max_concurrent, settings.test_runner_images
    )


def build_test_runner(
    settings: Settings, github: GitHubClient, sandbox: SandboxRunner | None = None
):
    return build_test_runner_server(
        github, sandbox or build_sandbox(settings), settings.test_runner_max_archive_mb << 20
    )


def build_providers(
    settings: Settings,
    github: GitHubClient | None = None,
    osv: OSVClient | None = None,
    sandbox: SandboxRunner | None = None,
) -> list[ToolProvider]:
    """Remote MCP servers when URLs are configured, otherwise the same servers in-process."""
    client = github or GitHubClient.create(settings.github_api_url, settings.github_token)
    if osv is None and settings.osv_api_url:
        osv = OSVClient.create(settings.osv_api_url)
    providers: list[ToolProvider] = [
        MCPProvider("github", settings.mcp_github_url or build_github_server(client)),
        MCPProvider("analysis", settings.mcp_analysis_url or build_analysis_server(client, osv)),
    ]
    if settings.test_runner_enabled:
        runner = settings.mcp_test_runner_url or build_test_runner(settings, client, sandbox)
        # Install + tests + image pull: well beyond the default tool timeout. Never retried.
        budget = (
            settings.test_runner_install_timeout_seconds
            + settings.test_runner_timeout_seconds
            + 600
        )
        providers.append(MCPProvider("test_runner", runner, timeout_seconds=budget, max_retries=0))
    return providers


@asynccontextmanager
async def open_checkpointer(settings: Settings):
    """Durable PostgreSQL checkpoints; in-memory only for SQLite-based local experiments."""
    if not settings.database_url.startswith("postgresql"):
        yield InMemorySaver()
        return
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    conninfo = settings.database_url.replace("+psycopg", "")
    async with AsyncConnectionPool(
        conninfo,
        max_size=max(2, settings.worker_concurrency * 2),
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    ) as pool:
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        yield saver
