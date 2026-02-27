"""
Cluster awareness tools (read-only).

Tools:
  k8s_cluster_info      — cluster endpoint, server version, current context
  k8s_get_contexts      — list kubeconfig contexts
  k8s_list_namespaces   — list namespaces with status
  k8s_list_nodes        — list nodes with roles, status, ages
  k8s_list_pods         — list pods (filter by namespace / label selector)
  k8s_list_deployments  — list deployments
  k8s_list_services     — list services
  k8s_list_events       — list events (filter by namespace / Warning-only)
"""

from __future__ import annotations

from mcp.types import TextContent, Tool

from k8s_mcp.kubectl import KubectlError, kubectl


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

AWARENESS_TOOLS: list[Tool] = [
    Tool(
        name="k8s_cluster_info",
        description=(
            "Show the current kubeconfig context, the Kubernetes server version, "
            "and the cluster API endpoint. Use this to confirm which cluster is active."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "context": {
                    "type": "string",
                    "description": "Kubeconfig context name. Defaults to current context.",
                },
            },
        },
    ),
    Tool(
        name="k8s_get_contexts",
        description="List all kubeconfig contexts and indicate which one is currently active.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="k8s_list_namespaces",
        description="List all namespaces in the cluster with their status and age.",
        inputSchema={
            "type": "object",
            "properties": {
                "context": {"type": "string", "description": "Kubeconfig context name."},
            },
        },
    ),
    Tool(
        name="k8s_list_nodes",
        description=(
            "List all nodes with their roles, status, Kubernetes version, OS, "
            "internal IP, and age. Useful for cluster topology and health overview."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "context": {"type": "string", "description": "Kubeconfig context name."},
            },
        },
    ),
    Tool(
        name="k8s_list_pods",
        description=(
            "List pods with their status, restart count, node assignment, and age. "
            "Filter by namespace or label selector. Use all_namespaces=true for a "
            "cluster-wide view."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Namespace to list pods in. Omit for current namespace.",
                },
                "all_namespaces": {
                    "type": "boolean",
                    "description": "List pods across all namespaces.",
                    "default": False,
                },
                "label_selector": {
                    "type": "string",
                    "description": "Label selector, e.g. 'app=nginx,env=prod'.",
                },
                "context": {"type": "string", "description": "Kubeconfig context name."},
            },
        },
    ),
    Tool(
        name="k8s_list_deployments",
        description=(
            "List deployments with desired/ready/available replica counts and age. "
            "Use all_namespaces=true for cluster-wide view."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "Namespace filter."},
                "all_namespaces": {"type": "boolean", "default": False},
                "context": {"type": "string"},
            },
        },
    ),
    Tool(
        name="k8s_list_services",
        description=(
            "List services with type, cluster IP, external IP/hostname, ports, and age."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "Namespace filter."},
                "all_namespaces": {"type": "boolean", "default": False},
                "context": {"type": "string"},
            },
        },
    ),
    Tool(
        name="k8s_list_events",
        description=(
            "List recent cluster events sorted by time. Set warnings_only=true to show "
            "only Warning-type events. Useful for spotting pod failures, OOM kills, "
            "scheduling issues, and other cluster activity."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Namespace to scope events. Omit for all namespaces.",
                },
                "all_namespaces": {"type": "boolean", "default": True},
                "warnings_only": {
                    "type": "boolean",
                    "description": "Show only Warning events.",
                    "default": False,
                },
                "context": {"type": "string"},
            },
        },
    ),
]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_cluster_info(args: dict) -> list[TextContent]:
    ctx = args.get("context")
    try:
        context_out, version_out, info_out = await _gather(
            kubectl(["config", "current-context"], context=ctx),
            kubectl(["version", "--short"], context=ctx),
            kubectl(["cluster-info"], context=ctx),
        )
    except KubectlError as e:
        return _err(str(e))

    text = f"Current context: {context_out}\n\n{version_out}\n\n{info_out}"
    return [TextContent(type="text", text=text)]


async def handle_get_contexts(_args: dict) -> list[TextContent]:
    try:
        out = await kubectl(["config", "get-contexts"])
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


async def handle_list_namespaces(args: dict) -> list[TextContent]:
    ctx = args.get("context")
    try:
        out = await kubectl(["get", "namespaces"], context=ctx)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


async def handle_list_nodes(args: dict) -> list[TextContent]:
    ctx = args.get("context")
    try:
        out = await kubectl(["get", "nodes", "-o", "wide"], context=ctx)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


async def handle_list_pods(args: dict) -> list[TextContent]:
    ctx = args.get("context")
    ns = args.get("namespace")
    all_ns = args.get("all_namespaces", False)
    selector = args.get("label_selector")

    cmd = ["get", "pods", "-o", "wide"]
    if selector:
        cmd += ["-l", selector]

    try:
        out = await kubectl(cmd, context=ctx, namespace=ns, all_namespaces=all_ns)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


async def handle_list_deployments(args: dict) -> list[TextContent]:
    ctx = args.get("context")
    ns = args.get("namespace")
    all_ns = args.get("all_namespaces", False)
    try:
        out = await kubectl(["get", "deployments"], context=ctx, namespace=ns, all_namespaces=all_ns)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


async def handle_list_services(args: dict) -> list[TextContent]:
    ctx = args.get("context")
    ns = args.get("namespace")
    all_ns = args.get("all_namespaces", False)
    try:
        out = await kubectl(["get", "services"], context=ctx, namespace=ns, all_namespaces=all_ns)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


async def handle_list_events(args: dict) -> list[TextContent]:
    ctx = args.get("context")
    ns = args.get("namespace")
    all_ns = args.get("all_namespaces", False)
    warnings_only = args.get("warnings_only", False)

    cmd = ["get", "events", "--sort-by=.lastTimestamp"]
    if warnings_only:
        cmd += ["--field-selector=type=Warning"]

    try:
        out = await kubectl(cmd, context=ctx, namespace=ns, all_namespaces=all_ns)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

AWARENESS_HANDLERS = {
    "k8s_cluster_info": handle_cluster_info,
    "k8s_get_contexts": handle_get_contexts,
    "k8s_list_namespaces": handle_list_namespaces,
    "k8s_list_nodes": handle_list_nodes,
    "k8s_list_pods": handle_list_pods,
    "k8s_list_deployments": handle_list_deployments,
    "k8s_list_services": handle_list_services,
    "k8s_list_events": handle_list_events,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402 (needed after TYPE_CHECKING guard)


async def _gather(*coros):
    return await asyncio.gather(*coros)


def _err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"Error: {msg}")]
