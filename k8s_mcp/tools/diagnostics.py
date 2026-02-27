"""
Diagnostic tools — read-only operations for troubleshooting.

Tools:
  k8s_describe      — kubectl describe any resource
  k8s_logs          — get pod logs (tail, container, previous)
  k8s_top_pods      — pod CPU/memory usage
  k8s_top_nodes     — node CPU/memory usage
  k8s_find_issues   — comprehensive cluster health scan
  k8s_get_yaml      — get resource as YAML
"""

from __future__ import annotations

import asyncio
import json

from mcp.types import TextContent, Tool

from k8s_mcp.kubectl import KubectlError, kubectl, kubectl_json
from k8s_mcp.formatters import node_conditions_summary, severity_icon


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

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
                "context": {"type": "string"},
            },
        },
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
    ),
    Tool(
        name="k8s_find_issues",
        description=(
            "Perform a comprehensive cluster health scan and report all detected problems. "
            "Checks: non-Running/non-Succeeded pods, high-restart pods, nodes with "
            "pressure/NotReady conditions, deployments with unavailable replicas, and "
            "recent Warning events. Run this first when diagnosing cluster problems."
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
    ),
    Tool(
        name="k8s_get_yaml",
        description="Get the full YAML definition of any Kubernetes resource.",
        inputSchema={
            "type": "object",
            "required": ["resource_type", "resource_name"],
            "properties": {
                "resource_type": {"type": "string", "description": "e.g. deployment, pod, configmap, secret."},
                "resource_name": {"type": "string"},
                "namespace": {"type": "string"},
                "context": {"type": "string"},
            },
        },
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


async def handle_logs(args: dict) -> list[TextContent]:
    pod = args["pod_name"]
    ctx = args.get("context")
    ns = args.get("namespace")
    container = args.get("container")
    tail = args.get("tail", 100)
    previous = args.get("previous", False)
    since = args.get("since")

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
    return [TextContent(type="text", text=out or "(no log output)")]


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

    # Run all checks in parallel
    results = await asyncio.gather(
        _check_pods(ctx, ns, all_ns, restart_threshold),
        _check_nodes(ctx),
        _check_deployments(ctx, ns, all_ns),
        _check_events(ctx, ns, all_ns),
        return_exceptions=True,
    )

    pod_issues, node_issues, deploy_issues, event_issues = results
    sections: list[str] = []

    def _add_section(title: str, items, icon: str):
        if isinstance(items, Exception):
            sections.append(f"{icon} {title}\n  (scan failed: {items})")
        elif items:
            body = "\n".join(f"  {line}" for line in items)
            sections.append(f"{icon} {title}\n{body}")

    _add_section("Pod Issues", pod_issues, severity_icon("critical"))
    _add_section("Node Issues", node_issues, severity_icon("critical"))
    _add_section("Deployment Issues", deploy_issues, severity_icon("warning"))
    _add_section("Recent Warning Events (last 20)", event_issues, severity_icon("warning"))

    if not sections:
        return [TextContent(type="text", text="No issues detected. Cluster looks healthy.")]

    header = f"Cluster Health Scan — {len([s for s in [pod_issues, node_issues, deploy_issues, event_issues] if s and not isinstance(s, Exception)])} categories with findings\n"
    report = header + "\n\n".join(sections)
    return [TextContent(type="text", text=report)]


async def handle_get_yaml(args: dict) -> list[TextContent]:
    rtype = args["resource_type"].lower()
    rname = args["resource_name"]
    ctx = args.get("context")
    ns = args.get("namespace")
    try:
        out = await kubectl(["get", rtype, rname, "-o", "yaml"], context=ctx, namespace=ns)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


# ---------------------------------------------------------------------------
# k8s_find_issues helpers
# ---------------------------------------------------------------------------

async def _check_pods(ctx, ns, all_ns, restart_threshold) -> list[str]:
    data = await kubectl_json(
        ["get", "pods"],
        context=ctx,
        namespace=ns,
        all_namespaces=all_ns,
    )
    items = data.get("items", [])
    issues: list[str] = []

    for pod in items:
        pod_ns = pod["metadata"]["namespace"]
        pod_name = pod["metadata"]["name"]
        phase = pod.get("status", {}).get("phase", "Unknown")

        if phase not in ("Running", "Succeeded", "Completed"):
            reason = pod.get("status", {}).get("reason", "")
            issues.append(f"[{pod_ns}] {pod_name}  phase={phase} {reason}")

        # Check high restarts
        for cs in pod.get("status", {}).get("containerStatuses", []):
            restarts = cs.get("restartCount", 0)
            cname = cs.get("name", "")
            if restarts >= restart_threshold:
                state = cs.get("state", {})
                waiting = state.get("waiting", {})
                reason = waiting.get("reason", "")
                issues.append(
                    f"[{pod_ns}] {pod_name}/{cname}  restarts={restarts}"
                    + (f" ({reason})" if reason else "")
                )

    return issues


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
                f"[{dep_ns}] {dep_name}  desired={desired} ready={ready} unavailable={unavailable}"
            )
        elif desired and ready == 0:
            issues.append(f"[{dep_ns}] {dep_name}  desired={desired} ready=0 (all replicas down)")

    return issues


async def _check_events(ctx, ns, all_ns) -> list[str]:
    try:
        out = await kubectl(
            ["get", "events", "--field-selector=type=Warning", "--sort-by=.lastTimestamp"],
            context=ctx,
            namespace=ns,
            all_namespaces=all_ns,
        )
    except KubectlError:
        return []

    lines = [l for l in out.splitlines() if l.strip() and not l.startswith("NAMESPACE")]
    return lines[-20:]  # last 20 warning events


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

DIAGNOSTIC_HANDLERS = {
    "k8s_describe": handle_describe,
    "k8s_logs": handle_logs,
    "k8s_top_pods": handle_top_pods,
    "k8s_top_nodes": handle_top_nodes,
    "k8s_find_issues": handle_find_issues,
    "k8s_get_yaml": handle_get_yaml,
}


def _err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"Error: {msg}")]
