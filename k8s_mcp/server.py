"""
Claude MCP Plugin — Kubernetes

Exposes kubectl-backed tools over MCP stdio transport across three categories:
  • Awareness   — cluster state, contexts, nodes, pods, services, events
  • Diagnostics — describe, logs, metrics, health scan, YAML export
  • Remediation — restart, scale, delete, rollback, apply, patch, node ops

Environment variables:
  K8S_MCP_READ_ONLY=true  — only register read-only tools

Run with:
    python -m k8s_mcp.server
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    ListToolsResult,
    TextContent,
)

from k8s_mcp.tools.awareness import AWARENESS_HANDLERS, AWARENESS_TOOLS
from k8s_mcp.tools.diagnostics import DIAGNOSTIC_HANDLERS, DIAGNOSTIC_TOOLS
from k8s_mcp.tools.remediation import REMEDIATION_HANDLERS, REMEDIATION_TOOLS

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

READ_ONLY = os.environ.get("K8S_MCP_READ_ONLY", "").lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

server = Server("kubernetes")

if READ_ONLY:
    ALL_TOOLS = AWARENESS_TOOLS + DIAGNOSTIC_TOOLS
    ALL_HANDLERS: dict = {**AWARENESS_HANDLERS, **DIAGNOSTIC_HANDLERS}
else:
    ALL_TOOLS = AWARENESS_TOOLS + DIAGNOSTIC_TOOLS + REMEDIATION_TOOLS
    ALL_HANDLERS = {
        **AWARENESS_HANDLERS,
        **DIAGNOSTIC_HANDLERS,
        **REMEDIATION_HANDLERS,
    }

WRITE_TOOLS = set(REMEDIATION_HANDLERS.keys())


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

    # Audit logging for write operations
    if name in WRITE_TOOLS:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        safe_args = {k: v for k, v in args.items() if k != "manifest"}
        if "manifest" in args:
            safe_args["manifest_size"] = f"{len(args['manifest'])} bytes"
        print(f"[AUDIT] {ts} {name} {safe_args}", file=sys.stderr)

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
    mode = "read-only" if READ_ONLY else "full"
    print(
        f"kubernetes MCP server starting — {len(ALL_TOOLS)} tools registered ({mode} mode)",
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
