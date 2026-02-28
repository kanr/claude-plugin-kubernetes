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
  k8s_delete_resource     — generic resource deletion (medium risk)
  k8s_diff                — diff manifest against live state (read-only)
"""

from __future__ import annotations

import os

import yaml
from mcp.types import TextContent, Tool, ToolAnnotations

from k8s_mcp.kubectl import (
    KubectlError,
    check_namespace_writable,
    kubectl,
    kubectl_diff,
    kubectl_json,
    kubectl_stdin,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BLOCKED_KINDS: set[str] = {
    "ClusterRole",
    "ClusterRoleBinding",
    "MutatingWebhookConfiguration",
    "ValidatingWebhookConfiguration",
    "CustomResourceDefinition",
    "PersistentVolume",
}


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
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="k8s_scale",
        description=(
            "[RISK: MEDIUM] Scale the replica count of a deployment or statefulset. "
            "Scaling to 0 stops all pods. Scaling down may affect availability. "
            "Scaling to 0 requires confirm_scale_to_zero=true."
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
                "confirm_scale_to_zero": {
                    "type": "boolean",
                    "description": "Required when replicas=0. Confirms intent to stop all pods.",
                    "default": False,
                },
                "namespace": {"type": "string"},
                "context": {"type": "string"},
            },
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="k8s_delete_pod",
        description=(
            "[RISK: LOW-MEDIUM] Delete a pod so that its controller (Deployment, "
            "StatefulSet, DaemonSet) recreates it. Use force=true to immediately "
            "terminate without graceful shutdown — use only for stuck/unresponsive pods. "
            "Note: if this is the only replica, there will be brief downtime until "
            "the controller recreates it."
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
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True),
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
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True),
    ),
    Tool(
        name="k8s_apply_manifest",
        description=(
            "[RISK: MEDIUM] Apply a Kubernetes manifest (YAML or JSON) to the cluster "
            "via `kubectl apply -f -`. Creates or updates resources. Use for deploying "
            "ConfigMaps, Deployments, Services, etc. Cluster-scoped resources "
            "(ClusterRole, MutatingWebhookConfiguration, etc.) are blocked by default."
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
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True),
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
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True),
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
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True),
    ),
    # --- New tools ---
    Tool(
        name="k8s_delete_resource",
        description=(
            "[RISK: MEDIUM] Delete a Kubernetes resource by type and name. "
            "The resource will be permanently removed. If managed by a controller, "
            "it may be recreated automatically."
        ),
        inputSchema={
            "type": "object",
            "required": ["resource_type", "resource_name"],
            "properties": {
                "resource_type": {"type": "string", "description": "Resource type, e.g. deployment, service, configmap, secret."},
                "resource_name": {"type": "string", "description": "Name of the resource to delete."},
                "namespace": {"type": "string"},
                "context": {"type": "string"},
            },
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True),
    ),
    Tool(
        name="k8s_diff",
        description=(
            "Show the diff between a manifest and the live cluster state using "
            "`kubectl diff`. Returns the unified diff output, or indicates no changes. "
            "Does not modify anything."
        ),
        inputSchema={
            "type": "object",
            "required": ["manifest"],
            "properties": {
                "manifest": {
                    "type": "string",
                    "description": "Full YAML or JSON manifest content to diff against live state.",
                },
                "namespace": {"type": "string"},
                "context": {"type": "string"},
            },
        },
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True),
    ),
]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_restart_deployment(args: dict) -> list[TextContent]:
    name = args["deployment_name"]
    ctx = args.get("context")
    ns = args.get("namespace")
    check_namespace_writable(ns)
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
    check_namespace_writable(ns)

    # Scale-to-zero confirmation gate
    if replicas == 0 and not args.get("confirm_scale_to_zero"):
        try:
            resource = await kubectl_json(
                ["get", f"{rtype}/{rname}"],
                context=ctx,
                namespace=ns,
            )
            current = resource.get("spec", {}).get("replicas", "unknown")
        except KubectlError:
            current = "unknown"
        return [TextContent(
            type="text",
            text=(
                f"WARNING: You are about to scale {rtype}/{rname} to 0 replicas "
                f"(currently {current}). This will stop ALL pods for this workload.\n\n"
                f"To confirm, re-call k8s_scale with confirm_scale_to_zero=true."
            ),
        )]

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
    check_namespace_writable(ns)

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
    check_namespace_writable(ns)

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
    check_namespace_writable(ns)

    # --- YAML pre-validation and resource type blocklist ---
    allow_cluster = os.environ.get("K8S_MCP_ALLOW_CLUSTER_RESOURCES", "").lower() == "true"
    try:
        docs = list(yaml.safe_load_all(manifest))
    except yaml.YAMLError as e:
        return _err(f"Invalid YAML manifest: {e}")

    for doc in docs:
        if doc is None:
            continue
        kind = doc.get("kind", "")

        # Block cluster-scoped resource kinds unless overridden
        if not allow_cluster and kind in _BLOCKED_KINDS:
            return _err(
                f"Resource kind '{kind}' is blocked by default. Blocked kinds: "
                f"{sorted(_BLOCKED_KINDS)}. Set env var K8S_MCP_ALLOW_CLUSTER_RESOURCES=true "
                f"to override."
            )

        # Check namespace from the document metadata
        doc_ns = (doc.get("metadata") or {}).get("namespace")
        if doc_ns:
            check_namespace_writable(doc_ns)

    # --- Apply ---
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
    check_namespace_writable(ns)

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

    # Drain can take a long time — use a 5 minute timeout
    timeout = 300 if operation == "drain" else None

    try:
        out = await kubectl(cmd, context=ctx, timeout_override=timeout)
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


async def handle_delete_resource(args: dict) -> list[TextContent]:
    rtype = args["resource_type"]
    rname = args["resource_name"]
    ctx = args.get("context")
    ns = args.get("namespace")
    check_namespace_writable(ns)

    try:
        out = await kubectl(
            ["delete", rtype, rname],
            context=ctx,
            namespace=ns,
        )
    except KubectlError as e:
        return _err(str(e))
    return [TextContent(type="text", text=out)]


async def handle_diff(args: dict) -> list[TextContent]:
    """Run kubectl diff. Special exit codes: 0=no diff, 1=has diff, >1=error."""
    manifest = args["manifest"]
    ctx = args.get("context")
    ns = args.get("namespace")

    try:
        returncode, stdout_text, stderr_text = await kubectl_diff(
            manifest, context=ctx, namespace=ns
        )
    except KubectlError as e:
        return _err(str(e))

    if returncode == 0:
        return [TextContent(type="text", text="No differences found. Live state matches the manifest.")]
    elif returncode == 1:
        # Exit code 1 means there IS a diff — stdout contains the unified diff
        return [TextContent(type="text", text=stdout_text if stdout_text else "Diff detected but output was empty.")]
    else:
        # Exit code >1 is an actual error
        return _err(stderr_text if stderr_text else f"kubectl diff exited with code {returncode}")


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
    "k8s_delete_resource": handle_delete_resource,
    "k8s_diff": handle_diff,
}


def _err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"Error: {msg}")]
