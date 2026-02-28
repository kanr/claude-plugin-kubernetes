"""
Cluster awareness tools (read-only).

Tools:
  k8s_cluster_info          — cluster endpoint, server version, current context
  k8s_get_contexts          — list kubeconfig contexts
  k8s_list_namespaces       — list namespaces with status
  k8s_list_nodes            — list nodes with roles, status, ages
  k8s_list_pods             — list pods (filter by namespace / label selector)
  k8s_list_deployments      — list deployments
  k8s_list_services         — list services
  k8s_list_events           — list events (filter by namespace / Warning-only)
  k8s_list_images           — list container images running across pods
  k8s_list_statefulsets     — list statefulsets
  k8s_list_ingresses        — list ingresses
  k8s_list_jobs             — list jobs
  k8s_list_cronjobs         — list cronjobs
  k8s_list_configmaps       — list configmaps (metadata only)
  k8s_list_secrets          — list secrets (metadata only, no data exposed)
  k8s_list_pvcs             — list persistent volume claims
  k8s_list_daemonsets       — list daemonsets
  k8s_list_hpa              — list horizontal pod autoscalers
  k8s_list_networkpolicies  — list network policies
  k8s_api_resources         — list available API resource types
  k8s_list_serviceaccounts  — list service accounts (RBAC)
  k8s_list_roles            — list RBAC roles (namespace-scoped)
  k8s_list_rolebindings     — list RBAC role bindings
  k8s_list_pvs              — list persistent volumes (cluster-scoped)
  k8s_list_storageclasses   — list storage classes
  k8s_list_resourcequotas   — list resource quotas
  k8s_list_limitranges      — list limit ranges
  k8s_list_poddisruptionbudgets — list pod disruption budgets
"""

from __future__ import annotations

import asyncio
from collections import Counter

from mcp.types import TextContent, Tool, ToolAnnotations

from k8s_mcp.formatters import _err
from k8s_mcp.kubectl import KubectlError, kubectl, kubectl_json


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _gather(*coros):
    return await asyncio.gather(*coros, return_exceptions=True)


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
    """Prepend a summary like '15 pods (12 Running, 2 Pending, 1 CrashLoopBackOff)'.

    Also appends next-step suggestions when unhealthy pods are detected.
    """
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

    # Next-step suggestions for unhealthy pods
    _UNHEALTHY = {"CrashLoopBackOff", "Error", "ImagePullBackOff", "ErrImagePull",
                  "Pending", "CreateContainerError", "OOMKilled", "Init:Error",
                  "Init:CrashLoopBackOff"}
    unhealthy = {s for s in counts if s in _UNHEALTHY}
    suggestions: list[str] = []
    if unhealthy:
        # Find pod names with unhealthy statuses
        name_idx = _find_col_index(headers, "NAME")
        ns_idx = _find_col_index(headers, "NAMESPACE")
        status_idx = _find_col_index(headers, "STATUS")
        if name_idx is not None and status_idx is not None:
            for row in rows[:3]:  # Suggest for first 3 unhealthy pods
                if status_idx < len(row) and row[status_idx] in _UNHEALTHY:
                    pod = row[name_idx] if name_idx < len(row) else "?"
                    ns_hint = f' namespace="{row[ns_idx]}"' if ns_idx is not None and ns_idx < len(row) else ""
                    suggestions.append(f'-> Suggested: k8s_describe resource_type="pod" resource_name="{pod}"{ns_hint}')
                    suggestions.append(f'-> Suggested: k8s_logs pod_name="{pod}"{ns_hint}')
                    break  # One pod example is enough
        if not suggestions:
            suggestions.append("-> Suggested: k8s_find_issues to identify root causes")

    result = f"{summary}\n\n{output}"
    if suggestions:
        result += "\n\n" + "\n".join(suggestions)
    return result


def _summarize_deployments(output: str) -> str:
    """Prepend a summary with total count and any degraded deployments."""
    headers, rows = _parse_table_rows(output)
    if not rows:
        return output
    ready_idx = _find_col_index(headers, "READY")
    name_idx = _find_col_index(headers, "NAME")
    ns_idx = _find_col_index(headers, "NAMESPACE")
    if ready_idx is None:
        return f"{len(rows)} deployments\n\n{output}"
    degraded = 0
    degraded_names: list[tuple[str, str | None]] = []
    for row in rows:
        if ready_idx < len(row):
            parts = row[ready_idx].split("/")
            if len(parts) == 2 and parts[0] != parts[1]:
                degraded += 1
                dep_name = row[name_idx] if name_idx is not None and name_idx < len(row) else None
                dep_ns = row[ns_idx] if ns_idx is not None and ns_idx < len(row) else None
                if dep_name and len(degraded_names) < 3:
                    degraded_names.append((dep_name, dep_ns))
    total = len(rows)
    if degraded:
        summary = f"{total} deployments ({degraded} degraded — ready != desired)"
    else:
        summary = f"{total} deployments (all healthy)"

    result = f"{summary}\n\n{output}"

    # Next-step suggestions for degraded deployments
    if degraded_names:
        suggestions: list[str] = []
        for dname, dns in degraded_names[:1]:  # Suggest for first degraded
            ns_hint = f' namespace="{dns}"' if dns else ""
            suggestions.append(f'-> Suggested: k8s_describe resource_type="deployment" resource_name="{dname}"{ns_hint}')
            suggestions.append(f"-> Suggested: k8s_find_issues to identify root causes")
        result += "\n\n" + "\n".join(suggestions)

    return result


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
        "namespace": {"type": "string", "description": "Kubernetes namespace. Defaults to current context's namespace."},
        "all_namespaces": {"type": "boolean", "default": False, "description": "Search across all namespaces. Default: false."},
        "context": {"type": "string", "description": "Kubernetes context to use. Defaults to current context."},
    },
}

_CLUSTER_SCOPED_SCHEMA = {
    "type": "object",
    "properties": {
        "context": {"type": "string", "description": "Kubernetes context to use. Defaults to current context."},
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
                "context": {"type": "string", "description": "Kubernetes context to use. Defaults to current context."},
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
                "context": {"type": "string", "description": "Kubernetes context to use. Defaults to current context."},
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
                    "description": "Kubernetes namespace. Defaults to current context's namespace.",
                },
                "all_namespaces": {
                    "type": "boolean",
                    "description": "Search across all namespaces. Default: false.",
                    "default": False,
                },
                "label_selector": {
                    "type": "string",
                    "description": "Label selector, e.g. 'app=nginx,env=prod'.",
                },
                "context": {"type": "string", "description": "Kubernetes context to use. Defaults to current context."},
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
                "namespace": {"type": "string", "description": "Kubernetes namespace. Defaults to current context's namespace."},
                "all_namespaces": {"type": "boolean", "default": False, "description": "Search across all namespaces. Default: false."},
                "context": {"type": "string", "description": "Kubernetes context to use. Defaults to current context."},
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
                "namespace": {"type": "string", "description": "Kubernetes namespace. Defaults to current context's namespace."},
                "all_namespaces": {"type": "boolean", "default": False, "description": "Search across all namespaces. Default: false."},
                "context": {"type": "string", "description": "Kubernetes context to use. Defaults to current context."},
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
                "context": {"type": "string", "description": "Kubernetes context to use. Defaults to current context."},
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
                "all_namespaces": {"type": "boolean", "default": True, "description": "Search across all namespaces. Default: true (unlike other tools)."},
                "warnings_only": {
                    "type": "boolean",
                    "description": "Show only Warning events.",
                    "default": False,
                },
                "context": {"type": "string", "description": "Kubernetes context to use. Defaults to current context."},
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
                "context": {"type": "string", "description": "Kubernetes context to use. Defaults to current context."},
            },
        },
        annotations=_RO_ANNOTATIONS,
    ),
    # --- RBAC tools ---
    Tool(
        name="k8s_list_serviceaccounts",
        description=(
            "List ServiceAccounts with their name and age. Useful for auditing "
            "workload identities and RBAC configurations."
        ),
        inputSchema=_NS_SCHEMA,
        annotations=_RO_ANNOTATIONS,
    ),
    Tool(
        name="k8s_list_roles",
        description=(
            "List Roles (namespace-scoped RBAC rules) with their name and age. "
            "Use k8s_describe to see the actual permissions granted."
        ),
        inputSchema=_NS_SCHEMA,
        annotations=_RO_ANNOTATIONS,
    ),
    Tool(
        name="k8s_list_rolebindings",
        description=(
            "List RoleBindings with their name and age. Shows which subjects "
            "(users, groups, service accounts) are bound to which roles."
        ),
        inputSchema=_NS_SCHEMA,
        annotations=_RO_ANNOTATIONS,
    ),
    # --- Storage tools ---
    Tool(
        name="k8s_list_pvs",
        description=(
            "List PersistentVolumes (cluster-scoped) with status, capacity, access modes, "
            "reclaim policy, and storage class. Useful for storage capacity planning."
        ),
        inputSchema=_CLUSTER_SCOPED_SCHEMA,
        annotations=_RO_ANNOTATIONS,
    ),
    Tool(
        name="k8s_list_storageclasses",
        description=(
            "List StorageClasses with provisioner, reclaim policy, and volume binding mode. "
            "Useful for understanding available storage tiers."
        ),
        inputSchema=_CLUSTER_SCOPED_SCHEMA,
        annotations=_RO_ANNOTATIONS,
    ),
    # --- Resource governance tools ---
    Tool(
        name="k8s_list_resourcequotas",
        description=(
            "List ResourceQuotas with their hard limits and current usage per namespace. "
            "Useful for identifying namespaces approaching CPU/memory/object limits."
        ),
        inputSchema=_NS_SCHEMA,
        annotations=_RO_ANNOTATIONS,
    ),
    Tool(
        name="k8s_list_limitranges",
        description=(
            "List LimitRanges that define default and maximum resource requests/limits "
            "for containers in a namespace."
        ),
        inputSchema=_NS_SCHEMA,
        annotations=_RO_ANNOTATIONS,
    ),
    Tool(
        name="k8s_list_poddisruptionbudgets",
        description=(
            "List PodDisruptionBudgets with min-available/max-unavailable settings. "
            "Important for understanding availability guarantees during node drain or "
            "rolling upgrades."
        ),
        inputSchema=_NS_SCHEMA,
        annotations=_RO_ANNOTATIONS,
    ),
    Tool(
        name="k8s_get",
        description=(
            "Get any Kubernetes resource type, including CRDs. Use this for resource "
            "types not covered by a dedicated list tool (e.g. replicasets, endpoints, "
            "Argo Workflows, Istio VirtualServices, Cert-Manager Certificates). "
            "Optionally specify a resource name to get a single resource. "
            "Supports label and field selectors for filtering."
        ),
        inputSchema={
            "type": "object",
            "required": ["resource_type"],
            "properties": {
                "resource_type": {
                    "type": "string",
                    "description": (
                        "Kubernetes resource type, e.g. 'replicasets', 'endpoints', "
                        "'virtualservices.networking.istio.io', 'certificates.cert-manager.io'."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": "Get a single resource by name. Omit to list all.",
                },
                "output": {
                    "type": "string",
                    "enum": ["wide", "yaml", "json", "name"],
                    "default": "wide",
                    "description": "Output format. 'wide' (default) for tabular, 'yaml'/'json' for full definition, 'name' for names only.",
                },
                "label_selector": {
                    "type": "string",
                    "description": "Label selector filter, e.g. 'app=nginx,env=prod'.",
                },
                "field_selector": {
                    "type": "string",
                    "description": "Field selector filter, e.g. 'status.phase=Running'.",
                },
                "namespace": {"type": "string", "description": "Kubernetes namespace. Defaults to current context's namespace."},
                "all_namespaces": {"type": "boolean", "default": False, "description": "Search across all namespaces."},
                "context": {"type": "string", "description": "Kubernetes context to use. Defaults to current context."},
            },
        },
        annotations=_RO_ANNOTATIONS,
    ),
    Tool(
        name="k8s_get_configmap_data",
        description=(
            "Read the data contents of a specific ConfigMap. Returns all key-value "
            "pairs stored in the ConfigMap. Use this to inspect configuration values, "
            "check for missing keys, or verify settings. For binary data keys, shows "
            "the key name and byte size only."
        ),
        inputSchema={
            "type": "object",
            "required": ["configmap_name"],
            "properties": {
                "configmap_name": {
                    "type": "string",
                    "description": "Name of the ConfigMap to read.",
                },
                "key": {
                    "type": "string",
                    "description": "Return only this specific key's value. Omit to return all keys.",
                },
                "namespace": {"type": "string", "description": "Kubernetes namespace. Defaults to current context's namespace."},
                "context": {"type": "string", "description": "Kubernetes context to use. Defaults to current context."},
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
    context_out, version_out, info_out = await _gather(
        kubectl(["config", "current-context"], context=ctx),
        kubectl(["version", "--short"], context=ctx),
        kubectl(["cluster-info"], context=ctx),
    )

    parts = []
    parts.append(
        f"Current context: {context_out}"
        if not isinstance(context_out, Exception)
        else f"Current context: (unavailable \u2014 {context_out})"
    )
    parts.append(
        version_out
        if not isinstance(version_out, Exception)
        else f"Version info: (unavailable \u2014 {version_out})"
    )
    parts.append(
        info_out
        if not isinstance(info_out, Exception)
        else f"Cluster info: (unavailable \u2014 {info_out})"
    )
    return [TextContent(type="text", text="\n\n".join(parts))]


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
    # Default to all namespaces when no specific namespace is requested, matching
    # the schema default of True. When a namespace is explicitly supplied, default
    # to using that namespace (all_namespaces=False) unless explicitly overridden.
    all_ns = args.get("all_namespaces", ns is None)
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

async def _simple_list(args: dict, resource: str, *, resource_label: str | None = None) -> list[TextContent]:
    """Generic handler for simple list operations. Prepends a count summary."""
    ctx = args.get("context")
    ns = args.get("namespace")
    all_ns = args.get("all_namespaces", False)
    try:
        out = await kubectl(["get", resource], context=ctx, namespace=ns, all_namespaces=all_ns)
    except KubectlError as e:
        return _err(str(e))
    # Add a summary header with the resource count
    label = resource_label or resource
    headers, rows = _parse_table_rows(out)
    if rows:
        scope = "all namespaces" if all_ns else (ns or "current namespace")
        summary = f"{len(rows)} {label} in {scope}"
        out = f"{summary}\n\n{out}"
    return [TextContent(type="text", text=out)]


async def handle_list_statefulsets(args: dict) -> list[TextContent]:
    return await _simple_list(args, "statefulsets", resource_label="statefulsets")


async def handle_list_ingresses(args: dict) -> list[TextContent]:
    return await _simple_list(args, "ingress", resource_label="ingresses")


async def handle_list_jobs(args: dict) -> list[TextContent]:
    return await _simple_list(args, "jobs", resource_label="jobs")


async def handle_list_cronjobs(args: dict) -> list[TextContent]:
    return await _simple_list(args, "cronjobs", resource_label="cronjobs")


async def handle_list_configmaps(args: dict) -> list[TextContent]:
    return await _simple_list(args, "configmaps", resource_label="configmaps")


async def handle_list_secrets(args: dict) -> list[TextContent]:
    return await _simple_list(args, "secrets", resource_label="secrets")


async def handle_list_pvcs(args: dict) -> list[TextContent]:
    return await _simple_list(args, "pvc", resource_label="PVCs")


async def handle_list_daemonsets(args: dict) -> list[TextContent]:
    return await _simple_list(args, "daemonsets", resource_label="daemonsets")


async def handle_list_hpa(args: dict) -> list[TextContent]:
    return await _simple_list(args, "hpa", resource_label="HPAs")


async def handle_list_networkpolicies(args: dict) -> list[TextContent]:
    return await _simple_list(args, "networkpolicies", resource_label="network policies")


async def handle_api_resources(args: dict) -> list[TextContent]:
    ctx = args.get("context")
    try:
        out = await kubectl(["api-resources"], context=ctx)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


# --- RBAC handlers ---

async def handle_list_serviceaccounts(args: dict) -> list[TextContent]:
    return await _simple_list(args, "serviceaccounts", resource_label="service accounts")


async def handle_list_roles(args: dict) -> list[TextContent]:
    return await _simple_list(args, "roles", resource_label="roles")


async def handle_list_rolebindings(args: dict) -> list[TextContent]:
    return await _simple_list(args, "rolebindings", resource_label="role bindings")


# --- Storage handlers ---

async def handle_list_pvs(args: dict) -> list[TextContent]:
    ctx = args.get("context")
    try:
        out = await kubectl(["get", "pv"], context=ctx)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


async def handle_list_storageclasses(args: dict) -> list[TextContent]:
    ctx = args.get("context")
    try:
        out = await kubectl(["get", "storageclasses"], context=ctx)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


# --- Resource governance handlers ---

async def handle_list_resourcequotas(args: dict) -> list[TextContent]:
    return await _simple_list(args, "resourcequotas", resource_label="resource quotas")


async def handle_list_limitranges(args: dict) -> list[TextContent]:
    return await _simple_list(args, "limitranges", resource_label="limit ranges")


async def handle_list_poddisruptionbudgets(args: dict) -> list[TextContent]:
    return await _simple_list(args, "poddisruptionbudgets", resource_label="pod disruption budgets")


async def handle_get(args: dict) -> list[TextContent]:
    """Generic get handler for any resource type including CRDs."""
    rtype = args["resource_type"]
    name = args.get("name")
    output = args.get("output", "wide")
    label_selector = args.get("label_selector")
    field_selector = args.get("field_selector")
    ctx = args.get("context")
    ns = args.get("namespace")
    all_ns = args.get("all_namespaces", False)

    cmd = ["get", rtype]
    if name:
        cmd.append(name)

    if output == "wide":
        cmd += ["-o", "wide"]
    elif output in ("yaml", "json"):
        cmd += ["-o", output]
    elif output == "name":
        cmd += ["-o", "name"]

    if label_selector:
        cmd += ["-l", label_selector]
    if field_selector:
        cmd += ["--field-selector", field_selector]

    try:
        out = await kubectl(cmd, context=ctx, namespace=ns, all_namespaces=all_ns)
    except KubectlError as e:
        return _err(str(e))

    # Strip managed fields from YAML output for readability
    if output == "yaml" and not name:
        return [TextContent(type="text", text=out)]

    if output == "yaml" and name:
        try:
            import yaml as _yaml
            data = _yaml.safe_load(out)
            if isinstance(data, dict):
                metadata = data.get("metadata", {})
                metadata.pop("managedFields", None)
                annotations = metadata.get("annotations", {})
                annotations.pop("kubectl.kubernetes.io/last-applied-configuration", None)
                if not annotations and "annotations" in metadata:
                    del metadata["annotations"]
                out = _yaml.dump(data, default_flow_style=False, sort_keys=False)
        except Exception:
            pass  # Return raw output on any parse error

    # Add summary for tabular outputs
    if output in ("wide", "name") and not name:
        headers, rows = _parse_table_rows(out)
        if rows:
            scope = "all namespaces" if all_ns else (ns or "current namespace")
            summary = f"{len(rows)} {rtype} in {scope}"
            out = f"{summary}\n\n{out}"

    return [TextContent(type="text", text=out)]


async def handle_get_configmap_data(args: dict) -> list[TextContent]:
    """Read the data contents of a ConfigMap."""
    cm_name = args["configmap_name"]
    key = args.get("key")
    ctx = args.get("context")
    ns = args.get("namespace")

    try:
        data = await kubectl_json(["get", "configmap", cm_name], context=ctx, namespace=ns)
    except KubectlError as e:
        return _err(str(e))

    cm_data = data.get("data") or {}
    binary_data = data.get("binaryData") or {}

    if key:
        if key in cm_data:
            return [TextContent(type="text", text=f"{key}:\n{cm_data[key]}")]
        elif key in binary_data:
            import base64
            size = len(base64.b64decode(binary_data[key]))
            return [TextContent(type="text", text=f"{key}: (binary data, {size} bytes)")]
        else:
            available = sorted(list(cm_data.keys()) + list(binary_data.keys()))
            return _err(f"Key '{key}' not found in ConfigMap '{cm_name}'. Available keys: {available}")

    parts: list[str] = []
    parts.append(f"ConfigMap: {cm_name}")
    parts.append(f"Keys: {len(cm_data) + len(binary_data)}")
    parts.append("")

    for k, v in cm_data.items():
        # Truncate very long values
        if len(v) > 2000:
            v_display = v[:2000] + f"\n... ({len(v)} bytes total, truncated)"
        else:
            v_display = v
        parts.append(f"--- {k} ---")
        parts.append(v_display)
        parts.append("")

    for k, v in binary_data.items():
        import base64
        size = len(base64.b64decode(v))
        parts.append(f"--- {k} (binary, {size} bytes) ---")
        parts.append("")

    return [TextContent(type="text", text="\n".join(parts).rstrip())]


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
    "k8s_list_serviceaccounts": handle_list_serviceaccounts,
    "k8s_list_roles": handle_list_roles,
    "k8s_list_rolebindings": handle_list_rolebindings,
    "k8s_list_pvs": handle_list_pvs,
    "k8s_list_storageclasses": handle_list_storageclasses,
    "k8s_list_resourcequotas": handle_list_resourcequotas,
    "k8s_list_limitranges": handle_list_limitranges,
    "k8s_list_poddisruptionbudgets": handle_list_poddisruptionbudgets,
    "k8s_get": handle_get,
    "k8s_get_configmap_data": handle_get_configmap_data,
}
