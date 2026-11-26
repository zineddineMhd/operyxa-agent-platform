"""Run an MCP server standalone:
python -m operyxa.mcp_servers {github|analysis|changes|test_runner} [port].

The test runner needs a Docker daemon. Run it on a dedicated machine (or with a gVisor runtime)
rather than next to the API: whoever controls the Docker socket controls that host.
"""

import sys

from operyxa.bootstrap import build_test_runner
from operyxa.config import get_settings
from operyxa.mcp_servers.analysis_server import build_analysis_server
from operyxa.mcp_servers.changes_server import build_changes_server
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
        "changes": build_changes_server,
        "test_runner": lambda c: build_test_runner(settings, c),
    }
    if len(sys.argv) < 2 or sys.argv[1] not in builders:
        raise SystemExit(
            "usage: python -m operyxa.mcp_servers {github|analysis|changes|test_runner} [port]"
        )
    server = builders[sys.argv[1]](client)
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8100
    server.run("streamable-http", host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
