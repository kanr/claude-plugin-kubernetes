"""
Cluster awareness tools (read-only).

Tools:
  k8s_cluster_info        — cluster endpoint, server version, current context
  k8s_get_contexts        — list kubeconfig contexts
  k8s_list_namespaces     — list namespaces with status
  k8s_list_nodes          — list nodes with roles, status, ages
  k8s_list_pods           — list pods (filter by namespace / label selector)
  k8s_list_deployments    — list deployments
  k8s_list_services       — list services
  k8s_list_events         — list events (filter by namespace / Warning-only)
  k8s_list_images         — list container images running across pods
  k8s_list_statefulsets   — list statefulsets
  k8s_list_ingresses      — list ingresses
  k8s_list_jobs           — list jobs
  k8s_list_cronjobs       — list cronjobs
  k8s_list_configmaps     — list configmaps (metadata only)
  k8s_list_secrets        — list secrets (metadata only, no data exposed)
  k8s_list_pvcs           — list persistent volume claims
  k8s_list_daemonsets     — list daemonsets
  k8s_list_hpa            — list horizontal pod autoscalers
  k8s_list_networkpolicies — list network policies
  k8s_api_resources       — list available API resource types
"""

from __future__ import annotations

import asyncio
from collections import Counter

from mcp.types import TextContent, Tool, ToolAnnotations

from k8s_mcp.kubectl import KubectlError, kubectl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _gather(*coros):
    return await asyncio.gather(*coros)


def _err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"Error: {msg}")]


def _find_col_index(headers: list[str], *candidates: str) -> int | None:
    """Find the index of a column by name (case-insensitive). Returns None if not found."""
    for candidate in candidates:
        for i, h in enumerate(headers):
            if h.upper() == candidate.upper():
                return i
    return None


def _parse_table_rows(output: str) -> tuple[list[str], list[list[str]]]:
    """Parse kubectl tabular output into header list and row-value lists.

    Returns (headers, rows) where rows is a list of split-line values.
    Handles the case where output is empty or has no data rows.
    """
    lines = output.strip().splitlines()
    if not lines:
        return [], []
    headers = lines[0].split()
    rows = [line.split() for line in lines[1:] if line.strip()]
    return headers, rows


def _col_values(headers: list[str], rows: list[list[str]], *col_names: str) -> list[str]:
    """Extract values from a specific column across all rows."""
    idx = _find_col_index(headers, *col_names)
    if idx is None:
        return []
    return [row[idx] for row in rows if idx < len(row)]


# ---------------------------------------------------------------------------
# Summary builders for existing list handlers
# ---------------------------------------------------------------------------

def _summarize_pods(output: str) -> str:
    """Prepend a summary like '15 pods (12 Running, 2 Pending, 1 CrashLoopBackOff)'."""
    headers, rows = _parse_table_rows(output)
    if not rows:
        return output
    statuses = _col_values(headers, rows, "STATUS")
    if not statuses:
        return output
    counts = Counter(statuses)
    total = len(statuses)
    parts = ", ".join(f"{v} {k}" for k, v in counts.most_common())
    summary = f"{total} pods ({parts})"
    return f"{summary}\n\n{output}"


def _summarize_deployments(output: str) -> str:
    """Prepend a summary with total count and any degraded deployments."""
    headers, rows = _parse_table_rows(output)
    if not rows:
        return output
    ready_idx = _find_col_index(headers, "READY")
    if ready_idx is None:
        return f"{len(rows)} deployments\n\n{output}"
    degraded = 0
    for row in rows:
        if ready_idx < len(row):
            parts = row[ready_idx].split("/")
            if len(parts) == 2 and parts[0] != parts[1]:
                degraded += 1
    total = len(rows)
    if degraded:
        summary = f"{total} deployments ({degraded} degraded — ready != desired)"
    else:
        summary = f"{total} deployments (all healthy)"
    return f"{summary}\n\n{output}"


def _summarize_nodes(output: str) -> str:
    """Prepend a summary counting Ready vs NotReady nodes."""
    headers, rows = _parse_table_rows(output)
    if not rows:
        return output
    statuses = _col_values(headers, rows, "STATUS")
    if not statuses:
        return output
    counts = Counter(statuses)
    total = len(statuses)
    ready = counts.get("Ready", 0)
    not_ready = total - ready
    if not_ready:
        summary = f"{total} nodes ({ready} Ready, {not_ready} NotReady)"
    else:
        summary = f"{total} nodes (all Ready)"
    return f"{summary}\n\n{output}"


def _summarize_services(output: str) -> str:
    """Prepend a summary counting services by TYPE."""
    headers, rows = _parse_table_rows(output)
    if not rows:
        return output
    types = _col_values(headers, rows, "TYPE")
    if not types:
        return f"{len(rows)} services\n\n{output}"
    counts = Counter(types)
    total = len(types)
    parts = ", ".join(f"{v} {k}" for k, v in counts.most_common())
    summary = f"{total} services ({parts})"
    return f"{summary}\n\n{output}"


def _summarize_events(output: str, warnings_only: bool) -> str:
    """Prepend a summary counting events."""
    headers, rows = _parse_table_rows(output)
    if not rows:
        return output
    total = len(rows)
    qualifier = " warning" if warnings_only else ""
    summary = f"{total}{qualifier} events"
    if not warnings_only:
        types = _col_values(headers, rows, "TYPE")
        if types:
            counts = Counter(types)
            parts = ", ".join(f"{v} {k}" for k, v in counts.most_common())
            summary = f"{total} events ({parts})"
    return f"{summary}\n\n{output}"


# ---------------------------------------------------------------------------
# Namespace/all_namespaces/context schema — reused across many tools
# ---------------------------------------------------------------------------

_NS_SCHEMA = {
    "type": "object",
    "properties": {
        "namespace": {"type": "string", "description": "Namespace filter."},
        "all_namespaces": {"type": "boolean", "default": False},
        "context": {"type": "string", "description": "Kubeconfig context name."},
    },
}

_RO_ANNOTATIONS = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)


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
        annotations=_RO_ANNOTATIONS,
    ),
    Tool(
        name="k8s_get_contexts",
        description="List all kubeconfig contexts and indicate which one is currently active.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
        annotations=_RO_ANNOTATIONS,
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
        annotations=_RO_ANNOTATIONS,
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
        annotations=_RO_ANNOTATIONS,
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
        annotations=_RO_ANNOTATIONS,
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
        annotations=_RO_ANNOTATIONS,
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
        annotations=_RO_ANNOTATIONS,
    ),
    Tool(
        name="k8s_list_images",
        description=(
            "List container images running across pods. Shows namespace, pod name, "
            "container name, and image for each container. Use all_namespaces=true "
            "for a cluster-wide view."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "Namespace filter."},
                "all_namespaces": {"type": "boolean", "default": False},
                "context": {"type": "string", "description": "Kubeconfig context name."},
            },
        },
        annotations=_RO_ANNOTATIONS,
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
        annotations=_RO_ANNOTATIONS,
    ),
    # --- New tools ---
    Tool(
        name="k8s_list_statefulsets",
        description=(
            "List StatefulSets with desired/ready replica counts and age. "
            "Use all_namespaces=true for cluster-wide view."
        ),
        inputSchema=_NS_SCHEMA,
        annotations=_RO_ANNOTATIONS,
    ),
    Tool(
        name="k8s_list_ingresses",
        description=(
            "List Ingress resources with hosts, paths, backends, and address info."
        ),
        inputSchema=_NS_SCHEMA,
        annotations=_RO_ANNOTATIONS,
    ),
    Tool(
        name="k8s_list_jobs",
        description=(
            "List Jobs with their completions, duration, and age."
        ),
        inputSchema=_NS_SCHEMA,
        annotations=_RO_ANNOTATIONS,
    ),
    Tool(
        name="k8s_list_cronjobs",
        description=(
            "List CronJobs with their schedule, last schedule time, and active job count."
        ),
        inputSchema=_NS_SCHEMA,
        annotations=_RO_ANNOTATIONS,
    ),
    Tool(
        name="k8s_list_configmaps",
        description=(
            "List ConfigMaps with their name and age. Shows metadata only, not data contents."
        ),
        inputSchema=_NS_SCHEMA,
        annotations=_RO_ANNOTATIONS,
    ),
    Tool(
        name="k8s_list_secrets",
        description=(
            "List Secrets with their name, type, and age. Shows metadata only — "
            "never exposes secret data."
        ),
        inputSchema=_NS_SCHEMA,
        annotations=_RO_ANNOTATIONS,
    ),
    Tool(
        name="k8s_list_pvcs",
        description=(
            "List PersistentVolumeClaims with status, volume, capacity, access modes, "
            "and storage class."
        ),
        inputSchema=_NS_SCHEMA,
        annotations=_RO_ANNOTATIONS,
    ),
    Tool(
        name="k8s_list_daemonsets",
        description=(
            "List DaemonSets with desired/current/ready counts and age."
        ),
        inputSchema=_NS_SCHEMA,
        annotations=_RO_ANNOTATIONS,
    ),
    Tool(
        name="k8s_list_hpa",
        description=(
            "List HorizontalPodAutoscalers with target, min/max replicas, "
            "current replicas, and metrics."
        ),
        inputSchema=_NS_SCHEMA,
        annotations=_RO_ANNOTATIONS,
    ),
    Tool(
        name="k8s_list_networkpolicies",
        description=(
            "List NetworkPolicies with pod selector and age."
        ),
        inputSchema=_NS_SCHEMA,
        annotations=_RO_ANNOTATIONS,
    ),
    Tool(
        name="k8s_api_resources",
        description=(
            "List available API resource types in the cluster (including CRDs). "
            "Useful for discovering what resource kinds are available."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "context": {"type": "string", "description": "Kubeconfig context name."},
            },
        },
        annotations=_RO_ANNOTATIONS,
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
    return [TextContent(type="text", text=_summarize_nodes(out))]


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
    return [TextContent(type="text", text=_summarize_pods(out))]


async def handle_list_deployments(args: dict) -> list[TextContent]:
    ctx = args.get("context")
    ns = args.get("namespace")
    all_ns = args.get("all_namespaces", False)
    try:
        out = await kubectl(["get", "deployments"], context=ctx, namespace=ns, all_namespaces=all_ns)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=_summarize_deployments(out))]


async def handle_list_services(args: dict) -> list[TextContent]:
    ctx = args.get("context")
    ns = args.get("namespace")
    all_ns = args.get("all_namespaces", False)
    try:
        out = await kubectl(["get", "services"], context=ctx, namespace=ns, all_namespaces=all_ns)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=_summarize_services(out))]


async def handle_list_images(args: dict) -> list[TextContent]:
    ctx = args.get("context")
    ns = args.get("namespace")
    all_ns = args.get("all_namespaces", False)

    cmd = [
        "get", "pods",
        "-o", "custom-columns="
              "NAMESPACE:.metadata.namespace,"
              "POD:.metadata.name,"
              "CONTAINER:.spec.containers[*].name,"
              "IMAGE:.spec.containers[*].image",
    ]

    try:
        out = await kubectl(cmd, context=ctx, namespace=ns, all_namespaces=all_ns)
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
    return [TextContent(type="text", text=_summarize_events(out, warnings_only))]


# --- New handlers ---

async def _simple_list(args: dict, resource: str) -> list[TextContent]:
    """Generic handler for simple list operations."""
    ctx = args.get("context")
    ns = args.get("namespace")
    all_ns = args.get("all_namespaces", False)
    try:
        out = await kubectl(["get", resource], context=ctx, namespace=ns, all_namespaces=all_ns)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


async def handle_list_statefulsets(args: dict) -> list[TextContent]:
    return await _simple_list(args, "statefulsets")


async def handle_list_ingresses(args: dict) -> list[TextContent]:
    return await _simple_list(args, "ingress")


async def handle_list_jobs(args: dict) -> list[TextContent]:
    return await _simple_list(args, "jobs")


async def handle_list_cronjobs(args: dict) -> list[TextContent]:
    return await _simple_list(args, "cronjobs")


async def handle_list_configmaps(args: dict) -> list[TextContent]:
    return await _simple_list(args, "configmaps")


async def handle_list_secrets(args: dict) -> list[TextContent]:
    return await _simple_list(args, "secrets")


async def handle_list_pvcs(args: dict) -> list[TextContent]:
    return await _simple_list(args, "pvc")


async def handle_list_daemonsets(args: dict) -> list[TextContent]:
    return await _simple_list(args, "daemonsets")


async def handle_list_hpa(args: dict) -> list[TextContent]:
    return await _simple_list(args, "hpa")


async def handle_list_networkpolicies(args: dict) -> list[TextContent]:
    return await _simple_list(args, "networkpolicies")


async def handle_api_resources(args: dict) -> list[TextContent]:
    ctx = args.get("context")
    try:
        out = await kubectl(["api-resources"], context=ctx)
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
    "k8s_list_images": handle_list_images,
    "k8s_list_events": handle_list_events,
    "k8s_list_statefulsets": handle_list_statefulsets,
    "k8s_list_ingresses": handle_list_ingresses,
    "k8s_list_jobs": handle_list_jobs,
    "k8s_list_cronjobs": handle_list_cronjobs,
    "k8s_list_configmaps": handle_list_configmaps,
    "k8s_list_secrets": handle_list_secrets,
    "k8s_list_pvcs": handle_list_pvcs,
    "k8s_list_daemonsets": handle_list_daemonsets,
    "k8s_list_hpa": handle_list_hpa,
    "k8s_list_networkpolicies": handle_list_networkpolicies,
    "k8s_api_resources": handle_api_resources,
}
