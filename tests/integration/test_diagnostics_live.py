"""
Integration tests for diagnostic handlers against live minikube cluster.
"""

from __future__ import annotations

import pytest
from mcp.types import TextContent

from k8s_mcp.tools.diagnostics import (
    handle_describe,
    handle_find_issues,
    handle_get_yaml,
    handle_logs,
)
from tests.integration.conftest import skip_no_cluster

pytestmark = [pytest.mark.integration, skip_no_cluster]

# A stable pod we can target in integration tests
COREDNS_NS = "kube-system"


async def _get_coredns_pod_name() -> str:
    """Helper to find the coredns pod name at runtime."""
    import subprocess, json
    result = subprocess.run(
        ["kubectl", "get", "pods", "-n", COREDNS_NS, "-l", "k8s-app=kube-dns", "-o", "json"],
        capture_output=True,
        timeout=10,
    )
    data = json.loads(result.stdout)
    return data["items"][0]["metadata"]["name"]


async def test_find_issues_live():
    result = await handle_find_issues({"context": "minikube"})
    assert isinstance(result[0], TextContent)
    # Either "No issues" or a report with section headers
    text = result[0].text
    assert "No issues" in text or "Issues" in text or "Pod" in text or "Node" in text


async def test_find_issues_scoped_to_namespace():
    result = await handle_find_issues({"namespace": COREDNS_NS, "context": "minikube"})
    assert isinstance(result[0], TextContent)
    assert "Error" not in result[0].text


async def test_describe_namespace_live():
    result = await handle_describe({
        "resource_type": "namespace",
        "resource_name": "default",
        "context": "minikube",
    })
    assert "default" in result[0].text
    assert "Active" in result[0].text


async def test_describe_node_live():
    result = await handle_describe({
        "resource_type": "node",
        "resource_name": "minikube",
        "context": "minikube",
    })
    assert "minikube" in result[0].text
    assert "Ready" in result[0].text


async def test_logs_live():
    pod_name = await _get_coredns_pod_name()
    result = await handle_logs({
        "pod_name": pod_name,
        "namespace": COREDNS_NS,
        "tail": 10,
        "context": "minikube",
    })
    assert isinstance(result[0], TextContent)
    assert "Error" not in result[0].text


async def test_get_yaml_live():
    result = await handle_get_yaml({
        "resource_type": "namespace",
        "resource_name": "default",
        "context": "minikube",
    })
    assert "apiVersion" in result[0].text
    assert "Namespace" in result[0].text
