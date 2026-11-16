"""Run an MCP server standalone: python -m operyxa.mcp_servers {github|analysis} [port]."""

import sys

from operyxa.config import get_settings
from operyxa.mcp_servers.analysis_server import build_analysis_server
from operyxa.mcp_servers.github_client import GitHubClient
from operyxa.mcp_servers.github_server import build_github_server
from operyxa.mcp_servers.osv import OSVClient


def main() -> None:
    settings = get_settings()
    client = GitHubClient.create(settings.github_api_url, settings.github_token)
    osv = OSVClient.create(settings.osv_api_url) if settings.osv_api_url else None
    builders = {
        "github": build_github_server,
        "analysis": lambda c: build_analysis_server(c, osv),
    }
    if len(sys.argv) < 2 or sys.argv[1] not in builders:
        raise SystemExit("usage: python -m operyxa.mcp_servers {github|analysis} [port]")
    server = builders[sys.argv[1]](client)
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8100
    server.run("streamable-http", host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
