"""Tool providers. The gateway only knows this small interface; MCP is one implementation."""

from dataclasses import dataclass
from typing import Any, Protocol

from mcp import Client


@dataclass(frozen=True)
class RemoteTool:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ProviderResult:
    is_error: bool
    text: str


class ToolProvider(Protocol):
    name: str

    async def list_tools(self) -> list[RemoteTool]: ...

    async def call(self, tool: str, args: dict[str, Any]) -> ProviderResult: ...


class MCPProvider:
    """`target` is a streamable-HTTP URL, or an MCPServer instance for in-process use.

    One short-lived client per call keeps failures isolated and avoids sharing a session
    across asyncio tasks; connection reuse is an optimisation for later.
    """

    def __init__(self, name: str, target: Any):
        self.name = name
        self._target = target

    async def list_tools(self) -> list[RemoteTool]:
        async with Client(self._target) as client:
            listing = await client.list_tools()
        return [
            RemoteTool(t.name, t.description or "", dict(t.input_schema)) for t in listing.tools
        ]

    async def call(self, tool: str, args: dict[str, Any]) -> ProviderResult:
        async with Client(self._target) as client:
            result = await client.call_tool(tool, args)
        text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
        return ProviderResult(bool(result.is_error), text)
