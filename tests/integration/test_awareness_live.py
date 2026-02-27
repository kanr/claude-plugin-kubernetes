"""
Integration tests for awareness handlers against live minikube cluster.
"""

from __future__ import annotations

import pytest
from mcp.types import TextContent

from k8s_mcp.tools.awareness import (
    handle_cluster_info,
    handle_get_contexts,
    handle_list_deployments,
    handle_list_events,
    handle_list_images,
    handle_list_namespaces,
    handle_list_nodes,
    handle_list_pods,
    handle_list_services,
)
from tests.integration.conftest import skip_no_cluster

pytestmark = [pytest.mark.integration, skip_no_cluster]


async def test_cluster_info_live():
    result = await handle_cluster_info({"context": "minikube"})
    assert isinstance(result[0], TextContent)
    assert "minikube" in result[0].text


async def test_list_nodes_live():
    result = await handle_list_nodes({"context": "minikube"})
    assert isinstance(result[0], TextContent)
    assert "minikube" in result[0].text
    assert "Ready" in result[0].text


async def test_list_namespaces_live():
    result = await handle_list_namespaces({"context": "minikube"})
    text = result[0].text
    assert "default" in text
    assert "kube-system" in text


async def test_list_pods_by_namespace():
    result = await handle_list_pods({"namespace": "kube-system", "context": "minikube"})
    text = result[0].text
    assert "coredns" in text


async def test_list_pods_all_namespaces_regression():
    """Regression: all_namespaces=True must not raise flag-ordering error."""
    result = await handle_list_pods({"all_namespaces": True, "context": "minikube"})
    assert "Error" not in result[0].text
    assert "Running" in result[0].text or "Completed" in result[0].text


async def test_list_events_by_namespace_no_error():
    """Regression: namespace arg must not trigger all_namespaces=True."""
    result = await handle_list_events({"namespace": "kube-system", "context": "minikube"})
    assert "Error" not in result[0].text


async def test_list_events_warnings_only():
    result = await handle_list_events({"warnings_only": True, "context": "minikube"})
    assert isinstance(result[0], TextContent)


async def test_list_deployments_by_namespace():
    result = await handle_list_deployments({"namespace": "kube-system", "context": "minikube"})
    assert isinstance(result[0], TextContent)


async def test_list_services_by_namespace():
    result = await handle_list_services({"namespace": "kube-system", "context": "minikube"})
    assert isinstance(result[0], TextContent)
    assert "kubernetes" in result[0].text or "kube-dns" in result[0].text


async def test_get_contexts():
    result = await handle_get_contexts({})
    assert "minikube" in result[0].text


async def test_list_images_all_namespaces():
    result = await handle_list_images({"all_namespaces": True, "context": "minikube"})
    assert "Error" not in result[0].text
    text = result[0].text
    assert "IMAGE" in text
    assert "coredns" in text.lower() or "kube-proxy" in text.lower()


async def test_list_images_by_namespace():
    result = await handle_list_images({"namespace": "kube-system", "context": "minikube"})
    assert "Error" not in result[0].text
    assert "IMAGE" in result[0].text
