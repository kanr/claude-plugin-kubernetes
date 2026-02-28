"""
Diagnostic tools — read-only operations for troubleshooting.

Tools:
  k8s_describe         — kubectl describe any resource
  k8s_logs             — get pod logs (tail, container, previous, error filtering)
  k8s_logs_selector    — get aggregated logs from pods matching a label selector
  k8s_top_pods         — pod CPU/memory usage
  k8s_top_nodes        — node CPU/memory usage
  k8s_find_issues      — comprehensive cluster health scan
  k8s_get_yaml         — get resource as YAML
  k8s_exec             — execute a command in a pod container (non-interactive)
  k8s_rollout_status   — check deployment rollout progress
  k8s_rollout_history  — view deployment rollout history
  k8s_self_test        — MCP plugin health check
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone

from mcp.types import TextContent, Tool, ToolAnnotations

from k8s_mcp.kubectl import KubectlError, kubectl, kubectl_json
from k8s_mcp.formatters import node_conditions_summary, severity_icon


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

_RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)

DIAGNOSTIC_TOOLS: list[Tool] = [
    Tool(
        name="k8s_describe",
        description=(
            "Run `kubectl describe` on any resource to get a human-readable summary "
            "including events, conditions, labels, and configuration. "
            "Examples: resource_type='pod', resource_type='deployment', "
            "resource_type='node', resource_type='service'."
        ),
        inputSchema={
            "type": "object",
            "required": ["resource_type", "resource_name"],
            "properties": {
                "resource_type": {
                    "type": "string",
                    "description": "Resource type, e.g. pod, deployment, node, service, pvc, configmap.",
                },
                "resource_name": {
                    "type": "string",
                    "description": "Name of the resource to describe.",
                },
                "namespace": {"type": "string", "description": "Namespace (omit for cluster-scoped resources)."},
                "context": {"type": "string"},
            },
        },
        annotations=_RO,
    ),
    Tool(
        name="k8s_logs",
        description=(
            "Fetch logs from a pod or specific container. "
            "Use tail to limit output, previous=true to get logs from a crashed container, "
            "and container to target a specific init or sidecar container."
        ),
        inputSchema={
            "type": "object",
            "required": ["pod_name"],
            "properties": {
                "pod_name": {"type": "string", "description": "Pod name."},
                "namespace": {"type": "string"},
                "container": {"type": "string", "description": "Container name (omit for single-container pods)."},
                "tail": {
                    "type": "integer",
                    "description": "Number of lines from the end to return. Default 100.",
                    "default": 100,
                },
                "previous": {
                    "type": "boolean",
                    "description": "Get logs from the previously terminated container instance.",
                    "default": False,
                },
                "since": {
                    "type": "string",
                    "description": "Show logs since a relative duration, e.g. '5m', '1h'.",
                },
                "filter": {
                    "type": "string",
                    "enum": ["errors"],
                    "description": "Filter log output to show only lines matching common error patterns.",
                },
                "context": {"type": "string"},
            },
        },
        annotations=_RO,
    ),
    Tool(
        name="k8s_top_pods",
        description="Show current CPU and memory usage for pods. Requires metrics-server.",
        inputSchema={
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "all_namespaces": {"type": "boolean", "default": False},
                "label_selector": {"type": "string"},
                "context": {"type": "string"},
            },
        },
        annotations=_RO,
    ),
    Tool(
        name="k8s_top_nodes",
        description="Show current CPU and memory usage per node. Requires metrics-server.",
        inputSchema={
            "type": "object",
            "properties": {
                "context": {"type": "string"},
            },
        },
        annotations=_RO,
    ),
    Tool(
        name="k8s_find_issues",
        description=(
            "Perform a comprehensive cluster health scan and report all detected problems. "
            "Checks: non-Running/non-Succeeded pods, high-restart pods, nodes with "
            "pressure/NotReady conditions, deployments/statefulsets/daemonsets with unavailable "
            "replicas, failed jobs, pending PVCs, and recent Warning events. "
            "Run this first when diagnosing cluster problems."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Scope scan to a namespace. Omit to scan all namespaces.",
                },
                "context": {"type": "string"},
                "restart_threshold": {
                    "type": "integer",
                    "description": "Flag pods with restart count above this number. Default 5.",
                    "default": 5,
                },
            },
        },
        annotations=_RO,
    ),
    Tool(
        name="k8s_get_yaml",
        description=(
            "Get the YAML definition of any Kubernetes resource. "
            "Managed fields and last-applied-configuration annotations are stripped by default "
            "to reduce noise. Set raw=true to get the full unfiltered YAML."
        ),
        inputSchema={
            "type": "object",
            "required": ["resource_type", "resource_name"],
            "properties": {
                "resource_type": {"type": "string", "description": "e.g. deployment, pod, configmap, secret."},
                "resource_name": {"type": "string"},
                "namespace": {"type": "string"},
                "context": {"type": "string"},
                "raw": {
                    "type": "boolean",
                    "description": "Return full unfiltered YAML including managed fields. Default false.",
                    "default": False,
                },
            },
        },
        annotations=_RO,
    ),
    Tool(
        name="k8s_exec",
        description=(
            "Execute a command inside a running pod container and return the output. "
            "Useful for live debugging: checking files, environment variables, network "
            "connectivity, and application state. Non-interactive only — runs the command "
            "via `sh -c` and returns output. "
            "Example: command='ls /app/config' or command='env | grep DB_'."
        ),
        inputSchema={
            "type": "object",
            "required": ["pod_name", "command"],
            "properties": {
                "pod_name": {"type": "string", "description": "Name of the pod."},
                "command": {
                    "type": "string",
                    "description": "Shell command to run inside the pod, e.g. 'ls /app' or 'cat /etc/hosts'.",
                },
                "container": {
                    "type": "string",
                    "description": "Container name (omit for single-container pods).",
                },
                "namespace": {"type": "string"},
                "context": {"type": "string"},
            },
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True),
    ),
    Tool(
        name="k8s_logs_selector",
        description=(
            "Fetch logs from all pods matching a label selector. Aggregates output from "
            "multiple pods, each log line prefixed with the pod name. Useful when "
            "investigating a failing deployment or service across its replica set. "
            "Example: label_selector='app=api,env=prod'."
        ),
        inputSchema={
            "type": "object",
            "required": ["label_selector"],
            "properties": {
                "label_selector": {
                    "type": "string",
                    "description": "Label selector to match pods, e.g. 'app=nginx' or 'app=api,env=prod'.",
                },
                "namespace": {"type": "string"},
                "container": {
                    "type": "string",
                    "description": "Container name (for multi-container pods).",
                },
                "tail": {
                    "type": "integer",
                    "description": "Lines from the end per pod. Default 50.",
                    "default": 50,
                },
                "since": {
                    "type": "string",
                    "description": "Show logs since a relative duration, e.g. '5m', '1h'.",
                },
                "context": {"type": "string"},
            },
        },
        annotations=_RO,
    ),
    Tool(
        name="k8s_self_test",
        description=(
            "Run a health check on the MCP plugin itself. Verifies kubectl binary, "
            "cluster connectivity, authentication, and metrics-server."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
        annotations=_RO,
    ),
    Tool(
        name="k8s_rollout_status",
        description=(
            "Check the rollout status of a deployment. Returns current rollout progress "
            "and whether the rollout completed successfully. Times out after 30 seconds."
        ),
        inputSchema={
            "type": "object",
            "required": ["deployment_name"],
            "properties": {
                "deployment_name": {"type": "string", "description": "Name of the deployment."},
                "namespace": {"type": "string"},
                "context": {"type": "string"},
            },
        },
        annotations=_RO,
    ),
    Tool(
        name="k8s_rollout_history",
        description=(
            "View the rollout history of a deployment, showing all recorded revisions. "
            "Useful for deciding which revision to roll back to."
        ),
        inputSchema={
            "type": "object",
            "required": ["deployment_name"],
            "properties": {
                "deployment_name": {"type": "string", "description": "Name of the deployment."},
                "namespace": {"type": "string"},
                "context": {"type": "string"},
            },
        },
        annotations=_RO,
    ),
]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_describe(args: dict) -> list[TextContent]:
    rtype = args["resource_type"].lower()
    rname = args["resource_name"]
    ctx = args.get("context")
    ns = args.get("namespace")
    try:
        out = await kubectl(["describe", rtype, rname], context=ctx, namespace=ns)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


_ERROR_PATTERNS = re.compile(
    r"ERROR|Exception|FATAL|panic:|Traceback|FAIL|error:|level=error|\"level\":\"error\"",
    re.IGNORECASE,
)


async def handle_logs(args: dict) -> list[TextContent]:
    pod = args["pod_name"]
    ctx = args.get("context")
    ns = args.get("namespace")
    container = args.get("container")
    tail = args.get("tail", 100)
    previous = args.get("previous", False)
    since = args.get("since")
    log_filter = args.get("filter")

    cmd = ["logs", pod, f"--tail={tail}"]
    if container:
        cmd += ["-c", container]
    if previous:
        cmd.append("--previous")
    if since:
        cmd += [f"--since={since}"]

    try:
        out = await kubectl(cmd, context=ctx, namespace=ns)
    except KubectlError as e:
        return _err(str(e))

    if not out:
        return [TextContent(type="text", text="(no log output)")]

    if log_filter == "errors":
        out = _filter_error_lines(out)

    return [TextContent(type="text", text=out)]


def _filter_error_lines(raw: str) -> str:
    """Filter log output to lines matching error patterns, with 2 lines of context."""
    lines = raw.splitlines()
    total = len(lines)
    matching_indices: set[int] = set()

    for i, line in enumerate(lines):
        if _ERROR_PATTERNS.search(line):
            # Include 2 lines of context before and after
            for j in range(max(0, i - 2), min(total, i + 3)):
                matching_indices.add(j)

    if not matching_indices:
        return f"{total} lines fetched, 0 match error patterns."

    match_count = sum(1 for i, line in enumerate(lines) if _ERROR_PATTERNS.search(line))
    sorted_indices = sorted(matching_indices)

    # Build output with gap markers
    result_lines: list[str] = []
    prev_idx = -2
    for idx in sorted_indices:
        if idx > prev_idx + 1:
            result_lines.append("---")
        result_lines.append(lines[idx])
        prev_idx = idx

    header = f"{total} lines fetched, {match_count} match error patterns (showing filtered)"
    return header + "\n" + "\n".join(result_lines)


async def handle_top_pods(args: dict) -> list[TextContent]:
    ctx = args.get("context")
    ns = args.get("namespace")
    all_ns = args.get("all_namespaces", False)
    selector = args.get("label_selector")

    cmd = ["top", "pods"]
    if selector:
        cmd += ["-l", selector]

    try:
        out = await kubectl(cmd, context=ctx, namespace=ns, all_namespaces=all_ns)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


async def handle_top_nodes(args: dict) -> list[TextContent]:
    ctx = args.get("context")
    try:
        out = await kubectl(["top", "nodes"], context=ctx)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


async def handle_find_issues(args: dict) -> list[TextContent]:
    ctx = args.get("context")
    ns = args.get("namespace")
    all_ns = ns is None
    restart_threshold = args.get("restart_threshold", 5)

    # Run all 8 checks in parallel
    results = await asyncio.gather(
        _check_pods(ctx, ns, all_ns, restart_threshold),
        _check_nodes(ctx),
        _check_deployments(ctx, ns, all_ns),
        _check_statefulsets(ctx, ns, all_ns),
        _check_daemonsets(ctx, ns, all_ns),
        _check_jobs(ctx, ns, all_ns),
        _check_pvcs(ctx, ns, all_ns),
        _check_events(ctx, ns, all_ns),
        return_exceptions=True,
    )

    pod_result, node_result, deploy_result, sts_result, ds_result, job_result, pvc_result, event_result = results

    # Unpack pod result — it returns (critical, warning) tuple
    if isinstance(pod_result, Exception):
        pod_critical: list[str] = []
        pod_warning: list[str] = []
        pod_error = pod_result
    else:
        pod_critical, pod_warning = pod_result
        pod_error = None

    # Unpack events — returns (event_lines, event_map) tuple
    if isinstance(event_result, Exception):
        event_lines: list[str] = []
        event_map: dict[str, list[str]] = {}
        event_error = event_result
    else:
        event_lines, event_map = event_result
        event_error = None

    # Cross-reference events with pod issues
    pod_critical = _cross_reference_events(pod_critical, event_map)
    pod_warning = _cross_reference_events(pod_warning, event_map)

    # Collect all issues into severity tiers
    critical_items: list[str] = []
    warning_items: list[str] = []
    node_items: list[str] = []

    # Pod critical issues
    if pod_error:
        critical_items.append(f"(pod scan failed: {pod_error})")
    else:
        critical_items.extend(pod_critical)

    # Pod warning issues
    if not pod_error:
        warning_items.extend(pod_warning)

    # Node issues — always in their own section but counted as critical
    if isinstance(node_result, Exception):
        node_items.append(f"(node scan failed: {node_result})")
    else:
        node_items.extend(node_result)

    # Deployment issues -> warning
    if isinstance(deploy_result, Exception):
        warning_items.append(f"(deployment scan failed: {deploy_result})")
    else:
        warning_items.extend(deploy_result)

    # StatefulSet issues -> warning
    if isinstance(sts_result, Exception):
        warning_items.append(f"(statefulset scan failed: {sts_result})")
    else:
        warning_items.extend(sts_result)

    # DaemonSet issues -> warning
    if isinstance(ds_result, Exception):
        warning_items.append(f"(daemonset scan failed: {ds_result})")
    else:
        warning_items.extend(ds_result)

    # Job issues -> warning
    if isinstance(job_result, Exception):
        warning_items.append(f"(job scan failed: {job_result})")
    else:
        warning_items.extend(job_result)

    # PVC issues -> warning
    if isinstance(pvc_result, Exception):
        warning_items.append(f"(pvc scan failed: {pvc_result})")
    else:
        warning_items.extend(pvc_result)

    total_issues = len(critical_items) + len(warning_items) + len(node_items)

    if total_issues == 0 and not event_lines:
        return [TextContent(type="text", text="No issues detected. Cluster looks healthy.")]

    n_critical = len(critical_items) + len(node_items)
    n_warning = len(warning_items)

    header = f"Cluster Health Scan — {total_issues} issues found ({n_critical} critical, {n_warning} warning)"
    sections: list[str] = [header]

    if critical_items:
        body = "\n".join(f"  {line}" for line in critical_items)
        sections.append(f"{severity_icon('critical')} CRITICAL:\n{body}")

    if warning_items:
        body = "\n".join(f"  {line}" for line in warning_items)
        sections.append(f"{severity_icon('warning')} WARNING:\n{body}")

    if node_items:
        body = "\n".join(f"  {line}" for line in node_items)
        sections.append(f"{severity_icon('critical')} NODE ISSUES:\n{body}")

    if event_lines:
        if event_error:
            sections.append(f"{severity_icon('warning')} RECENT WARNING EVENTS (last 20)\n  (event scan failed: {event_error})")
        else:
            body = "\n".join(f"  {line}" for line in event_lines)
            sections.append(f"{severity_icon('warning')} RECENT WARNING EVENTS (last 20):\n{body}")

    report = "\n\n".join(sections)
    return [TextContent(type="text", text=report)]


async def handle_get_yaml(args: dict) -> list[TextContent]:
    rtype = args["resource_type"].lower()
    rname = args["resource_name"]
    ctx = args.get("context")
    ns = args.get("namespace")
    raw = args.get("raw", False)

    if raw:
        try:
            out = await kubectl(["get", rtype, rname, "-o", "yaml"], context=ctx, namespace=ns)
        except KubectlError as e:
            return _err(str(e))
        return [TextContent(type="text", text=out)]

    # Fetch as JSON, strip noise fields, convert to YAML
    try:
        data = await kubectl_json(["get", rtype, rname], context=ctx, namespace=ns)
    except KubectlError as e:
        return _err(str(e))

    metadata = data.get("metadata", {})
    metadata.pop("managedFields", None)
    annotations = metadata.get("annotations", {})
    annotations.pop("kubectl.kubernetes.io/last-applied-configuration", None)
    if not annotations and "annotations" in metadata:
        del metadata["annotations"]

    import yaml
    out = yaml.dump(data, default_flow_style=False, sort_keys=False)
    return [TextContent(type="text", text=out)]


async def handle_self_test(args: dict) -> list[TextContent]:
    """Run health checks on the MCP plugin itself."""
    import os
    from k8s_mcp.tools.awareness import AWARENESS_TOOLS
    from k8s_mcp.tools.remediation import REMEDIATION_TOOLS

    read_only = os.environ.get("K8S_MCP_READ_ONLY", "").lower() in ("1", "true", "yes")
    if read_only:
        total_tools = len(DIAGNOSTIC_TOOLS) + len(AWARENESS_TOOLS)
    else:
        total_tools = len(DIAGNOSTIC_TOOLS) + len(AWARENESS_TOOLS) + len(REMEDIATION_TOOLS)

    # Run all 4 checks in parallel
    results = await asyncio.gather(
        kubectl(["version", "--client", "--short"]),
        kubectl(["cluster-info"], timeout_override=5),
        kubectl(["auth", "can-i", "get", "pods", "--all-namespaces"]),
        kubectl(["top", "nodes"]),
        return_exceptions=True,
    )

    version_result, cluster_result, auth_result, metrics_result = results

    lines: list[str] = ["Plugin Self-Test Results"]

    # kubectl binary
    if isinstance(version_result, Exception):
        lines.append(f"  kubectl binary:     FAILED ({version_result})")
    else:
        # Extract version string — first line typically has version info
        ver = version_result.strip().splitlines()[0] if version_result.strip() else "unknown"
        lines.append(f"  kubectl binary:     OK ({ver})")

    # Cluster connection
    if isinstance(cluster_result, Exception):
        lines.append(f"  Cluster connection: FAILED ({cluster_result})")
    else:
        # Extract cluster endpoint from cluster-info output
        first_line = cluster_result.strip().splitlines()[0] if cluster_result.strip() else ""
        # Try to extract URL from the output
        url_match = re.search(r"https?://[^\s]+", first_line)
        endpoint = url_match.group(0) if url_match else "connected"
        lines.append(f"  Cluster connection: OK ({endpoint})")

    # Authentication
    if isinstance(auth_result, Exception):
        lines.append(f"  Authentication:     FAILED ({auth_result})")
    else:
        result_text = auth_result.strip().lower()
        if result_text == "yes":
            lines.append("  Authentication:     OK (can list pods)")
        else:
            lines.append(f"  Authentication:     LIMITED ({auth_result.strip()})")

    # Metrics server
    if isinstance(metrics_result, Exception):
        lines.append("  Metrics server:     NOT AVAILABLE")
    else:
        lines.append("  Metrics server:     OK")

    lines.append(f"  Tools registered:   {total_tools}")

    return [TextContent(type="text", text="\n".join(lines))]


# ---------------------------------------------------------------------------
# k8s_find_issues helpers
# ---------------------------------------------------------------------------

def _format_age(timestamp_str: str) -> str:
    """Convert an ISO timestamp to a human-readable relative age string."""
    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - ts
        total_seconds = int(delta.total_seconds())
        if total_seconds < 60:
            return f"{total_seconds}s"
        elif total_seconds < 3600:
            return f"{total_seconds // 60}m"
        elif total_seconds < 86400:
            return f"{total_seconds // 3600}h"
        else:
            return f"{total_seconds // 86400}d"
    except (ValueError, TypeError):
        return "unknown"


def _cross_reference_events(issues: list[str], event_map: dict[str, list[str]]) -> list[str]:
    """Append related event info to issue lines when a matching event exists."""
    if not event_map:
        return issues
    enriched: list[str] = []
    for line in issues:
        matched = False
        for obj_name, event_entries in event_map.items():
            if obj_name in line and event_entries:
                # Take the most recent related event
                enriched.append(line)
                enriched.append(f"    -> Related event: {event_entries[-1]}")
                matched = True
                break
        if not matched:
            enriched.append(line)
    return enriched


async def _check_pods(ctx, ns, all_ns, restart_threshold) -> tuple[list[str], list[str]]:
    """Check pods and split issues into critical and warning severity."""
    data = await kubectl_json(
        ["get", "pods"],
        context=ctx,
        namespace=ns,
        all_namespaces=all_ns,
    )
    items = data.get("items", [])
    critical: list[str] = []
    warning: list[str] = []

    _CRITICAL_REASONS = {"CrashLoopBackOff", "Error", "OOMKilled", "ImagePullBackOff", "ErrImagePull", "CreateContainerError"}

    for pod in items:
        pod_ns = pod["metadata"]["namespace"]
        pod_name = pod["metadata"]["name"]
        phase = pod.get("status", {}).get("phase", "Unknown")

        # Check container statuses for critical states first
        container_critical = False
        for cs in pod.get("status", {}).get("containerStatuses", []):
            cname = cs.get("name", "")
            restarts = cs.get("restartCount", 0)
            state = cs.get("state", {})
            waiting = state.get("waiting", {})
            terminated = state.get("terminated", {})
            reason = waiting.get("reason", "") or terminated.get("reason", "")

            if reason in _CRITICAL_REASONS:
                container_critical = True
                msg = f"[{pod_ns}/{pod_name}] {reason}"
                if reason == "CrashLoopBackOff" or reason in ("Error", "OOMKilled"):
                    msg += f' — container "{cname}" restarted {restarts} times'
                    msg += f'\n    -> Suggested: k8s_logs pod_name="{pod_name}" namespace="{pod_ns}" previous=true'
                elif reason in ("ImagePullBackOff", "ErrImagePull"):
                    image = cs.get("image", "unknown")
                    msg += f' — image "{image}" not found'
                    msg += f'\n    -> Suggested: k8s_describe resource_type="pod" resource_name="{pod_name}" namespace="{pod_ns}"'
                elif reason == "CreateContainerError":
                    detail = waiting.get("message", "")
                    msg += f" — {detail}" if detail else ""
                    msg += f'\n    -> Suggested: k8s_describe resource_type="pod" resource_name="{pod_name}" namespace="{pod_ns}"'
                critical.append(msg)

            elif restarts >= restart_threshold:
                critical.append(
                    f"[{pod_ns}/{pod_name}] container \"{cname}\" restarted {restarts} times"
                    + (f" ({reason})" if reason else "")
                    + f'\n    -> Suggested: k8s_logs pod_name="{pod_name}" namespace="{pod_ns}" previous=true'
                )
                container_critical = True

        # Non-running pods that aren't already flagged as critical
        if phase not in ("Running", "Succeeded", "Completed") and not container_critical:
            pod_reason = pod.get("status", {}).get("reason", "")
            line = f"[{pod_ns}/{pod_name}] phase={phase}"
            if pod_reason:
                line += f" ({pod_reason})"
            warning.append(line)

    return critical, warning


async def _check_nodes(ctx) -> list[str]:
    data = await kubectl_json(["get", "nodes"], context=ctx)
    items = data.get("items", [])
    issues: list[str] = []

    for node in items:
        name = node["metadata"]["name"]
        conditions = node.get("status", {}).get("conditions", [])
        summary = node_conditions_summary(conditions)
        if "ISSUES" in summary:
            issues.append(f"{name}: {summary}")

    return issues


async def _check_deployments(ctx, ns, all_ns) -> list[str]:
    data = await kubectl_json(
        ["get", "deployments"],
        context=ctx,
        namespace=ns,
        all_namespaces=all_ns,
    )
    items = data.get("items", [])
    issues: list[str] = []

    for dep in items:
        dep_ns = dep["metadata"]["namespace"]
        dep_name = dep["metadata"]["name"]
        status = dep.get("status", {})
        unavailable = status.get("unavailableReplicas", 0)
        desired = dep.get("spec", {}).get("replicas", 0)
        ready = status.get("readyReplicas", 0)

        if unavailable and unavailable > 0:
            issues.append(
                f"[{dep_ns}/{dep_name}] {unavailable}/{desired} replicas unavailable"
            )
        elif desired and ready == 0:
            issues.append(f"[{dep_ns}/{dep_name}] {desired}/{desired} replicas unavailable (all replicas down)")

    return issues


async def _check_statefulsets(ctx, ns, all_ns) -> list[str]:
    """Check statefulsets for unavailable replicas."""
    data = await kubectl_json(
        ["get", "statefulsets"],
        context=ctx,
        namespace=ns,
        all_namespaces=all_ns,
    )
    items = data.get("items", [])
    issues: list[str] = []

    for sts in items:
        sts_ns = sts["metadata"]["namespace"]
        sts_name = sts["metadata"]["name"]
        status = sts.get("status", {})
        desired = sts.get("spec", {}).get("replicas", 0)
        ready = status.get("readyReplicas", 0) or 0

        if desired and ready < desired:
            unavailable = desired - ready
            issues.append(
                f"[{sts_ns}/{sts_name}] statefulset {unavailable}/{desired} replicas unavailable"
            )

    return issues


async def _check_daemonsets(ctx, ns, all_ns) -> list[str]:
    """Check DaemonSets for nodes where pods are not ready."""
    data = await kubectl_json(
        ["get", "daemonsets"],
        context=ctx,
        namespace=ns,
        all_namespaces=all_ns,
    )
    items = data.get("items", [])
    issues: list[str] = []

    for ds in items:
        ds_ns = ds["metadata"]["namespace"]
        ds_name = ds["metadata"]["name"]
        status = ds.get("status", {})
        desired = status.get("desiredNumberScheduled", 0)
        ready = status.get("numberReady", 0) or 0

        if desired > 0 and ready < desired:
            unavailable = desired - ready
            issues.append(
                f"[{ds_ns}/{ds_name}] DaemonSet {unavailable}/{desired} pods not ready"
            )

    return issues


async def _check_jobs(ctx, ns, all_ns) -> list[str]:
    """Check jobs for failures and stuck states."""
    data = await kubectl_json(
        ["get", "jobs"],
        context=ctx,
        namespace=ns,
        all_namespaces=all_ns,
    )
    items = data.get("items", [])
    issues: list[str] = []

    for job in items:
        job_ns = job["metadata"]["namespace"]
        job_name = job["metadata"]["name"]
        status = job.get("status", {})
        failed = status.get("failed", 0) or 0
        succeeded = status.get("succeeded", 0) or 0
        active = status.get("active", 0) or 0
        conditions = status.get("conditions", [])

        # Check for Failed condition first (most specific)
        failed_condition = None
        for cond in conditions:
            if cond.get("type") == "Failed" and cond.get("status") == "True":
                failed_condition = cond
                break

        if failed_condition:
            reason = failed_condition.get("reason", "unknown")
            issues.append(f"[{job_ns}/{job_name}] Job failed ({reason})")
        elif failed > 0 and succeeded == 0:
            issues.append(f"[{job_ns}/{job_name}] Job has {failed} failure(s), no successes")
        elif active == 0 and succeeded == 0 and failed == 0:
            # Job exists but has no active, succeeded, or failed pods — stuck
            # Only flag if the job doesn't have a completionTime (not already done)
            if not status.get("completionTime"):
                issues.append(f"[{job_ns}/{job_name}] Job appears stuck (no active/succeeded/failed pods)")

    return issues


async def _check_pvcs(ctx, ns, all_ns) -> list[str]:
    """Check PVCs for non-Bound phases."""
    data = await kubectl_json(
        ["get", "pvc"],
        context=ctx,
        namespace=ns,
        all_namespaces=all_ns,
    )
    items = data.get("items", [])
    issues: list[str] = []

    for pvc in items:
        pvc_ns = pvc["metadata"]["namespace"]
        pvc_name = pvc["metadata"]["name"]
        phase = pvc.get("status", {}).get("phase", "Unknown")

        if phase != "Bound":
            # Try to get storage class info for context
            sc = pvc.get("spec", {}).get("storageClassName", "")
            detail = f' (storageclass "{sc}")' if sc else ""
            issues.append(f"[{pvc_ns}/{pvc_name}] PVC phase={phase}{detail}")

    return issues


async def _check_events(ctx, ns, all_ns) -> tuple[list[str], dict[str, list[str]]]:
    """Fetch warning events as JSON and return formatted lines plus a name->messages map."""
    try:
        data = await kubectl_json(
            ["get", "events", "--field-selector=type=Warning", "--sort-by=.lastTimestamp"],
            context=ctx,
            namespace=ns,
            all_namespaces=all_ns,
        )
    except KubectlError:
        return [], {}

    items = data.get("items", [])

    # Build a map of involvedObject.name -> list of event descriptions
    event_map: dict[str, list[str]] = {}
    formatted_lines: list[str] = []

    for event in items:
        obj = event.get("involvedObject", {})
        obj_name = obj.get("name", "")
        obj_kind = obj.get("kind", "")
        obj_ns = event.get("metadata", {}).get("namespace", "")
        reason = event.get("reason", "")
        message = event.get("message", "")
        last_ts = event.get("lastTimestamp", "") or event.get("metadata", {}).get("creationTimestamp", "")
        count = event.get("count", 1)
        age = _format_age(last_ts) if last_ts else "unknown"

        line = f"[{obj_ns}/{obj_name}] {obj_kind} {reason}: {message}"
        if count and count > 1:
            line += f" (x{count})"
        line += f" ({age} ago)"

        formatted_lines.append(line)

        # Index by object name for cross-referencing
        entry = f"{reason}: {message} ({age} ago)"
        event_map.setdefault(obj_name, []).append(entry)

    # Return last 20 events
    return formatted_lines[-20:], event_map


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

async def handle_exec(args: dict) -> list[TextContent]:
    pod = args["pod_name"]
    command = args["command"]
    ctx = args.get("context")
    ns = args.get("namespace")
    container = args.get("container")

    cmd = ["exec", pod]
    if container:
        cmd += ["-c", container]
    cmd += ["--", "sh", "-c", command]

    try:
        out = await kubectl(cmd, context=ctx, namespace=ns)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out if out else "(no output)")]


async def handle_logs_selector(args: dict) -> list[TextContent]:
    selector = args["label_selector"]
    ctx = args.get("context")
    ns = args.get("namespace")
    container = args.get("container")
    tail = args.get("tail", 50)
    since = args.get("since")

    cmd = ["logs", "-l", selector, f"--tail={tail}", "--prefix=true"]
    if container:
        cmd += ["-c", container]
    if since:
        cmd += [f"--since={since}"]

    try:
        out = await kubectl(cmd, context=ctx, namespace=ns)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out if out else "(no log output)")]


async def handle_rollout_status(args: dict) -> list[TextContent]:
    name = args["deployment_name"]
    ctx = args.get("context")
    ns = args.get("namespace")
    try:
        out = await kubectl(
            ["rollout", "status", f"deployment/{name}", "--timeout=30s"],
            context=ctx,
            namespace=ns,
        )
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


async def handle_rollout_history(args: dict) -> list[TextContent]:
    name = args["deployment_name"]
    ctx = args.get("context")
    ns = args.get("namespace")
    try:
        out = await kubectl(
            ["rollout", "history", f"deployment/{name}"],
            context=ctx,
            namespace=ns,
        )
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


DIAGNOSTIC_HANDLERS = {
    "k8s_describe": handle_describe,
    "k8s_logs": handle_logs,
    "k8s_top_pods": handle_top_pods,
    "k8s_top_nodes": handle_top_nodes,
    "k8s_find_issues": handle_find_issues,
    "k8s_get_yaml": handle_get_yaml,
    "k8s_exec": handle_exec,
    "k8s_logs_selector": handle_logs_selector,
    "k8s_self_test": handle_self_test,
    "k8s_rollout_status": handle_rollout_status,
    "k8s_rollout_history": handle_rollout_history,
}


def _err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"Error: {msg}")]
