"""
MCP Resources — expose Kubernetes cluster state as browsable resources.

Static resources:
  k8s://contexts          — list kubeconfig contexts
  k8s://cluster-info      — current context + server version
  k8s://namespaces        — namespace list

Resource templates (parameterized):
  k8s://namespaces/{namespace}/pods        — pods in a namespace
  k8s://namespaces/{namespace}/deployments — deployments in a namespace
  k8s://namespaces/{namespace}/services    — services in a namespace
  k8s://namespaces/{namespace}/events      — events in a namespace
"""

from __future__ import annotations

import re

from mcp.types import Resource, ResourceTemplate

from k8s_mcp.kubectl import KubectlError, kubectl


# ---------------------------------------------------------------------------
# Static resources
# ---------------------------------------------------------------------------

STATIC_RESOURCES: list[Resource] = [
    Resource(
        uri="k8s://contexts",
        name="Kubernetes Contexts",
        description="List all kubeconfig contexts and indicate which one is currently active.",
        mimeType="text/plain",
    ),
    Resource(
        uri="k8s://cluster-info",
        name="Cluster Info",
        description="Current kubeconfig context, Kubernetes server version, and cluster API endpoint.",
        mimeType="text/plain",
    ),
    Resource(
        uri="k8s://namespaces",
        name="Namespaces",
        description="List all namespaces in the cluster with their status and age.",
        mimeType="text/plain",
    ),
]


# ---------------------------------------------------------------------------
# Resource templates
# ---------------------------------------------------------------------------

RESOURCE_TEMPLATES: list[ResourceTemplate] = [
    ResourceTemplate(
        uriTemplate="k8s://namespaces/{namespace}/pods",
        name="Pods in namespace",
        description="List pods with status, restart count, node assignment, and age for a given namespace.",
        mimeType="text/plain",
    ),
    ResourceTemplate(
        uriTemplate="k8s://namespaces/{namespace}/deployments",
        name="Deployments in namespace",
        description="List deployments with replica counts and age for a given namespace.",
        mimeType="text/plain",
    ),
    ResourceTemplate(
        uriTemplate="k8s://namespaces/{namespace}/services",
        name="Services in namespace",
        description="List services with type, cluster IP, ports, and age for a given namespace.",
        mimeType="text/plain",
    ),
    ResourceTemplate(
        uriTemplate="k8s://namespaces/{namespace}/events",
        name="Events in namespace",
        description="List recent events sorted by time for a given namespace.",
        mimeType="text/plain",
    ),
]


# ---------------------------------------------------------------------------
# URI patterns for matching
# ---------------------------------------------------------------------------

_TEMPLATE_PATTERN = re.compile(
    r"^k8s://namespaces/(?P<namespace>[^/]+)/(?P<kind>pods|deployments|services|events)$"
)


# ---------------------------------------------------------------------------
# Resource reader
# ---------------------------------------------------------------------------

async def read_resource(uri: str) -> str:
    """Read a resource by URI and return its content as text."""
    # Strip the AnyUrl wrapper if needed — convert to plain string
    uri_str = str(uri)

    # Static resources
    if uri_str == "k8s://contexts":
        return await _read_contexts()
    elif uri_str == "k8s://cluster-info":
        return await _read_cluster_info()
    elif uri_str == "k8s://namespaces":
        return await _read_namespaces()

    # Template resources
    match = _TEMPLATE_PATTERN.match(uri_str)
    if match:
        namespace = match.group("namespace")
        kind = match.group("kind")
        return await _read_namespaced(namespace, kind)

    raise ValueError(f"Unknown resource URI: {uri_str}")


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

async def _read_contexts() -> str:
    try:
        out = await kubectl(["config", "get-contexts"])
    except KubectlError as e:
        return f"Error reading contexts: {e}"
    return out


async def _read_cluster_info() -> str:
    try:
        ctx = await kubectl(["config", "current-context"])
        version = await kubectl(["version", "--short"])
        cluster = await kubectl(["cluster-info"], timeout_override=5)
    except KubectlError as e:
        return f"Error reading cluster info: {e}"
    return f"Current context: {ctx}\n\n{version}\n\n{cluster}"


async def _read_namespaces() -> str:
    try:
        out = await kubectl(["get", "namespaces"])
    except KubectlError as e:
        return f"Error reading namespaces: {e}"
    return out


async def _read_namespaced(namespace: str, kind: str) -> str:
    """Read a namespaced resource list (pods, deployments, services, events)."""
    cmd = ["get", kind]
    try:
        out = await kubectl(cmd, namespace=namespace)
    except KubectlError as e:
        return f"Error reading {kind} in namespace '{namespace}': {e}"
    return out or f"No {kind} found in namespace '{namespace}'."
