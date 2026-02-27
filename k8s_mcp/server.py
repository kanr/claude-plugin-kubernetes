"""
Claude MCP Plugin — Kubernetes

Exposes kubectl-backed tools over MCP stdio transport across three categories:
  • Awareness   — cluster state, contexts, nodes, pods, services, events
  • Diagnostics — describe, logs, metrics, health scan, YAML export
  • Remediation — restart, scale, delete, rollback, apply, patch, node ops

Environment variables:
  K8S_MCP_READ_ONLY=true           — only register read-only tools
  K8S_MCP_ALLOWED_CONTEXTS=a,b     — restrict which kubeconfig contexts can be used
  K8S_MCP_NAMESPACE_BLOCKLIST=...  — block writes to namespaces (default: kube-system,kube-public,kube-node-lease)
  K8S_MCP_NAMESPACE_ALLOWLIST=...  — exclusive write allowlist (if set, only these are writable)

Run with:
    python -m k8s_mcp.server
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from datetime import datetime, timezone

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    GetPromptResult,
    ListToolsResult,
    TextContent,
)

from k8s_mcp.prompts import ALL_PROMPTS, get_prompt
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
# Prompts
# ---------------------------------------------------------------------------


@server.list_prompts()
async def list_prompts() -> list:
    return ALL_PROMPTS


@server.get_prompt()
async def handle_get_prompt(
    name: str, arguments: dict[str, str] | None
) -> GetPromptResult:
    return get_prompt(name, arguments)


# ---------------------------------------------------------------------------
# Startup preflight
# ---------------------------------------------------------------------------

async def _preflight() -> None:
    """Check kubectl availability and cluster connectivity before serving."""
    if not shutil.which("kubectl"):
        print(
            "FATAL: kubectl not found on PATH. Install kubectl and try again.",
            file=sys.stderr,
        )
        sys.exit(1)

    from k8s_mcp.kubectl import kubectl, KubectlError

    try:
        version = await kubectl(["version", "--client", "--short"])
        print(f"kubectl client: {version}", file=sys.stderr)
    except KubectlError as e:
        print(f"WARNING: kubectl version check failed: {e}", file=sys.stderr)

    try:
        await kubectl(["cluster-info"], timeout_override=5)
        print("Cluster connectivity: OK", file=sys.stderr)
    except KubectlError:
        print(
            "WARNING: Cluster unreachable. Tools will fail until a valid context is configured.",
            file=sys.stderr,
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
    await _preflight()
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
