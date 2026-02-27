"""
Remediation tools — write operations that modify cluster state.

All tools include risk level in their descriptions.
Node drain is the most disruptive and requires explicit confirmation via the
ignore_daemonsets and delete_emptydir_data flags.

Tools:
  k8s_restart_deployment   — rollout restart (low risk)
  k8s_scale               — scale replicas (medium risk)
  k8s_delete_pod          — delete pod to force recreation (low-medium risk)
  k8s_rollback_deployment  — rollout undo (medium risk)
  k8s_apply_manifest      — apply YAML via stdin (medium risk)
  k8s_patch_resource      — JSON merge patch (medium risk)
  k8s_node_operation      — cordon / uncordon / drain (high risk for drain)
"""

from __future__ import annotations

from mcp.types import TextContent, Tool

from k8s_mcp.kubectl import KubectlError, kubectl, kubectl_stdin


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

REMEDIATION_TOOLS: list[Tool] = [
    Tool(
        name="k8s_restart_deployment",
        description=(
            "[RISK: LOW] Perform a rolling restart of a deployment by triggering a "
            "new rollout. Pods are replaced one at a time; no downtime for deployments "
            "with multiple replicas and a proper update strategy."
        ),
        inputSchema={
            "type": "object",
            "required": ["deployment_name"],
            "properties": {
                "deployment_name": {"type": "string", "description": "Name of the deployment to restart."},
                "namespace": {"type": "string"},
                "context": {"type": "string"},
            },
        },
    ),
    Tool(
        name="k8s_scale",
        description=(
            "[RISK: MEDIUM] Scale the replica count of a deployment or statefulset. "
            "Scaling to 0 stops all pods. Scaling down may affect availability."
        ),
        inputSchema={
            "type": "object",
            "required": ["resource_type", "resource_name", "replicas"],
            "properties": {
                "resource_type": {
                    "type": "string",
                    "enum": ["deployment", "statefulset"],
                    "description": "Type of workload to scale.",
                },
                "resource_name": {"type": "string", "description": "Name of the workload."},
                "replicas": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Target replica count.",
                },
                "namespace": {"type": "string"},
                "context": {"type": "string"},
            },
        },
    ),
    Tool(
        name="k8s_delete_pod",
        description=(
            "[RISK: LOW-MEDIUM] Delete a pod so that its controller (Deployment, "
            "StatefulSet, DaemonSet) recreates it. Use force=true to immediately "
            "terminate without graceful shutdown — use only for stuck/unresponsive pods."
        ),
        inputSchema={
            "type": "object",
            "required": ["pod_name"],
            "properties": {
                "pod_name": {"type": "string", "description": "Name of the pod to delete."},
                "namespace": {"type": "string"},
                "force": {
                    "type": "boolean",
                    "description": "Force immediate deletion (grace-period=0). Use for stuck pods only.",
                    "default": False,
                },
                "context": {"type": "string"},
            },
        },
    ),
    Tool(
        name="k8s_rollback_deployment",
        description=(
            "[RISK: MEDIUM] Roll back a deployment to its previous revision or a specific "
            "revision number. Use after a bad release causes pod failures."
        ),
        inputSchema={
            "type": "object",
            "required": ["deployment_name"],
            "properties": {
                "deployment_name": {"type": "string"},
                "revision": {
                    "type": "integer",
                    "description": "Revision number to roll back to. Omit to roll back to the previous revision.",
                },
                "namespace": {"type": "string"},
                "context": {"type": "string"},
            },
        },
    ),
    Tool(
        name="k8s_apply_manifest",
        description=(
            "[RISK: MEDIUM] Apply a Kubernetes manifest (YAML or JSON) to the cluster "
            "via `kubectl apply -f -`. Creates or updates resources. Use for deploying "
            "ConfigMaps, Deployments, Services, etc."
        ),
        inputSchema={
            "type": "object",
            "required": ["manifest"],
            "properties": {
                "manifest": {
                    "type": "string",
                    "description": "Full YAML or JSON manifest content to apply.",
                },
                "namespace": {
                    "type": "string",
                    "description": "Namespace override (only applies to namespace-scoped resources without an explicit namespace in the manifest).",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Perform a server-side dry run without applying changes.",
                    "default": False,
                },
                "context": {"type": "string"},
            },
        },
    ),
    Tool(
        name="k8s_patch_resource",
        description=(
            "[RISK: MEDIUM] Apply a strategic merge patch to any resource. Provide the "
            "patch as a JSON string. Example: patch a deployment's image with "
            "'{\"spec\":{\"template\":{\"spec\":{\"containers\":[{\"name\":\"app\",\"image\":\"nginx:1.25\"}]}}}}'."
        ),
        inputSchema={
            "type": "object",
            "required": ["resource_type", "resource_name", "patch"],
            "properties": {
                "resource_type": {"type": "string", "description": "e.g. deployment, configmap, service."},
                "resource_name": {"type": "string"},
                "patch": {
                    "type": "string",
                    "description": "JSON merge patch string.",
                },
                "patch_type": {
                    "type": "string",
                    "enum": ["merge", "json", "strategic"],
                    "default": "merge",
                    "description": "Patch type. 'merge' (default) is JSON merge patch. 'strategic' is Kubernetes strategic merge patch.",
                },
                "namespace": {"type": "string"},
                "context": {"type": "string"},
            },
        },
    ),
    Tool(
        name="k8s_node_operation",
        description=(
            "[RISK: HIGH for drain] Perform node maintenance operations:\n"
            "  • cordon   — mark node unschedulable (new pods won't be placed here)\n"
            "  • uncordon — mark node schedulable again\n"
            "  • drain    — evict all pods from the node (causes pod disruption); "
            "requires ignore_daemonsets=true. Set delete_emptydir_data=true if pods "
            "use emptyDir volumes."
        ),
        inputSchema={
            "type": "object",
            "required": ["operation", "node_name"],
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["cordon", "uncordon", "drain"],
                    "description": "Operation to perform on the node.",
                },
                "node_name": {"type": "string", "description": "Name of the node."},
                "ignore_daemonsets": {
                    "type": "boolean",
                    "description": "Required for drain: ignore DaemonSet-managed pods.",
                    "default": False,
                },
                "delete_emptydir_data": {
                    "type": "boolean",
                    "description": "For drain: allow deletion of pods with emptyDir volumes.",
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

async def handle_restart_deployment(args: dict) -> list[TextContent]:
    name = args["deployment_name"]
    ctx = args.get("context")
    ns = args.get("namespace")
    try:
        out = await kubectl(["rollout", "restart", f"deployment/{name}"], context=ctx, namespace=ns)
        status = await kubectl(["rollout", "status", f"deployment/{name}", "--timeout=10s"], context=ctx, namespace=ns)
        return [TextContent(type="text", text=f"{out}\n{status}")]
    except KubectlError as e:
        return _err(str(e))


async def handle_scale(args: dict) -> list[TextContent]:
    rtype = args["resource_type"]
    rname = args["resource_name"]
    replicas = int(args["replicas"])
    ctx = args.get("context")
    ns = args.get("namespace")
    try:
        out = await kubectl(
            ["scale", f"{rtype}/{rname}", f"--replicas={replicas}"],
            context=ctx,
            namespace=ns,
        )
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


async def handle_delete_pod(args: dict) -> list[TextContent]:
    pod = args["pod_name"]
    ctx = args.get("context")
    ns = args.get("namespace")
    force = args.get("force", False)

    cmd = ["delete", "pod", pod]
    if force:
        cmd += ["--grace-period=0", "--force"]

    try:
        out = await kubectl(cmd, context=ctx, namespace=ns)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


async def handle_rollback_deployment(args: dict) -> list[TextContent]:
    name = args["deployment_name"]
    ctx = args.get("context")
    ns = args.get("namespace")
    revision = args.get("revision")

    cmd = ["rollout", "undo", f"deployment/{name}"]
    if revision is not None:
        cmd += [f"--to-revision={int(revision)}"]

    try:
        out = await kubectl(cmd, context=ctx, namespace=ns)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


async def handle_apply_manifest(args: dict) -> list[TextContent]:
    manifest = args["manifest"]
    ctx = args.get("context")
    ns = args.get("namespace")
    dry_run = args.get("dry_run", False)

    cmd = ["apply", "-f", "-"]
    if dry_run:
        cmd += ["--dry-run=server"]

    try:
        out = await kubectl_stdin(cmd, stdin_data=manifest, context=ctx, namespace=ns)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


async def handle_patch_resource(args: dict) -> list[TextContent]:
    rtype = args["resource_type"]
    rname = args["resource_name"]
    patch = args["patch"]
    patch_type = args.get("patch_type", "merge")
    ctx = args.get("context")
    ns = args.get("namespace")

    try:
        out = await kubectl(
            ["patch", rtype, rname, f"--type={patch_type}", "-p", patch],
            context=ctx,
            namespace=ns,
        )
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


async def handle_node_operation(args: dict) -> list[TextContent]:
    operation = args["operation"]
    node = args["node_name"]
    ctx = args.get("context")
    ignore_ds = args.get("ignore_daemonsets", False)
    delete_emptydir = args.get("delete_emptydir_data", False)

    if operation == "cordon":
        cmd = ["cordon", node]
    elif operation == "uncordon":
        cmd = ["uncordon", node]
    elif operation == "drain":
        cmd = ["drain", node]
        if ignore_ds:
            cmd.append("--ignore-daemonsets")
        if delete_emptydir:
            cmd.append("--delete-emptydir-data")
    else:
        return _err(f"Unknown operation: {operation}. Must be cordon, uncordon, or drain.")

    try:
        out = await kubectl(cmd, context=ctx)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

REMEDIATION_HANDLERS = {
    "k8s_restart_deployment": handle_restart_deployment,
    "k8s_scale": handle_scale,
    "k8s_delete_pod": handle_delete_pod,
    "k8s_rollback_deployment": handle_rollback_deployment,
    "k8s_apply_manifest": handle_apply_manifest,
    "k8s_patch_resource": handle_patch_resource,
    "k8s_node_operation": handle_node_operation,
}


def _err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"Error: {msg}")]
