"""
Claude MCP Plugin — Kubernetes

Exposes 21 tools across three categories:
  • Awareness   (8) — cluster state, contexts, nodes, pods, services, events
  • Diagnostics (6) — describe, logs, metrics, health scan, YAML export
  • Remediation (7) — restart, scale, delete, rollback, apply, patch, node ops

Run with:
    python -m src.server
"""

from __future__ import annotations

import asyncio
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    ListToolsResult,
    TextContent,
)

from src.tools.awareness import AWARENESS_HANDLERS, AWARENESS_TOOLS
from src.tools.diagnostics import DIAGNOSTIC_HANDLERS, DIAGNOSTIC_TOOLS
from src.tools.remediation import REMEDIATION_HANDLERS, REMEDIATION_TOOLS

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

server = Server("kubernetes")

ALL_TOOLS = AWARENESS_TOOLS + DIAGNOSTIC_TOOLS + REMEDIATION_TOOLS

ALL_HANDLERS: dict = {
    **AWARENESS_HANDLERS,
    **DIAGNOSTIC_HANDLERS,
    **REMEDIATION_HANDLERS,
}


@server.list_tools()
async def list_tools() -> ListToolsResult:
    return ListToolsResult(tools=ALL_TOOLS)


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> CallToolResult:
    args = arguments

    handler = ALL_HANDLERS.get(name)
    if handler is None:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Unknown tool: {name}")],
            isError=True,
        )

    try:
        content = await handler(args)
        return CallToolResult(content=content)
    except Exception as exc:  # noqa: BLE001
        return CallToolResult(
            content=[TextContent(type="text", text=f"Unexpected error: {exc}")],
            isError=True,
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _run() -> None:
    print(
        f"kubernetes MCP server starting — {len(ALL_TOOLS)} tools registered",
        file=sys.stderr,
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
