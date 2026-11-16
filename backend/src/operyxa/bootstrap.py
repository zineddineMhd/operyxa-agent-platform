"""Wiring shared by the worker and the tests."""

from contextlib import asynccontextmanager

from langgraph.checkpoint.memory import InMemorySaver

from operyxa.config import Settings
from operyxa.gateway.providers import MCPProvider, ToolProvider
from operyxa.mcp_servers.analysis_server import build_analysis_server
from operyxa.mcp_servers.github_client import GitHubClient
from operyxa.mcp_servers.github_server import build_github_server
from operyxa.mcp_servers.osv import OSVClient


def build_providers(
    settings: Settings, github: GitHubClient | None = None, osv: OSVClient | None = None
) -> list[ToolProvider]:
    """Remote MCP servers when URLs are configured, otherwise the same servers in-process."""
    client = github or GitHubClient.create(settings.github_api_url, settings.github_token)
    if osv is None and settings.osv_api_url:
        osv = OSVClient.create(settings.osv_api_url)
    return [
        MCPProvider("github", settings.mcp_github_url or build_github_server(client)),
        MCPProvider("analysis", settings.mcp_analysis_url or build_analysis_server(client, osv)),
    ]


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
