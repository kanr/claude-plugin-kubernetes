"""
Unit tests for src/tools/awareness.py handlers.
kubectl is patched at the module level so no real subprocess is spawned.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, call, patch

import pytest
from mcp.types import TextContent

from k8s_mcp.kubectl import KubectlError
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


def _ok(text: str = "ok output"):
    return AsyncMock(return_value=text)


def _err_mock():
    return AsyncMock(side_effect=KubectlError("something went wrong"))


# ---------------------------------------------------------------------------
# handle_cluster_info
# ---------------------------------------------------------------------------

async def test_handle_cluster_info_success():
    with patch("k8s_mcp.tools.awareness.kubectl", new_callable=AsyncMock) as mock_kctl:
        mock_kctl.side_effect = ["minikube", "v1.27.4", "control plane running"]
        result = await handle_cluster_info({})

    assert len(result) == 1
    assert isinstance(result[0], TextContent)
    text = result[0].text
    assert "minikube" in text
    assert "v1.27.4" in text
    assert "control plane running" in text


async def test_handle_cluster_info_error():
    with patch("k8s_mcp.tools.awareness.kubectl", side_effect=KubectlError("connection refused")):
        result = await handle_cluster_info({})

    assert "Error" in result[0].text


async def test_handle_cluster_info_passes_context():
    with patch("k8s_mcp.tools.awareness.kubectl", new_callable=AsyncMock) as mock_kctl:
        mock_kctl.side_effect = ["ctx", "ver", "info"]
        await handle_cluster_info({"context": "prod"})

    # All three calls should have received context="prod"
    for c in mock_kctl.call_args_list:
        assert c.kwargs.get("context") == "prod"


# ---------------------------------------------------------------------------
# handle_get_contexts
# ---------------------------------------------------------------------------

async def test_handle_get_contexts_success():
    with patch("k8s_mcp.tools.awareness.kubectl", return_value="CURRENT   NAME\n*   minikube"):
        result = await handle_get_contexts({})
    assert "minikube" in result[0].text


# ---------------------------------------------------------------------------
# handle_list_namespaces
# ---------------------------------------------------------------------------

async def test_handle_list_namespaces_success():
    with patch("k8s_mcp.tools.awareness.kubectl", return_value="default   Active"):
        result = await handle_list_namespaces({})
    assert "default" in result[0].text


# ---------------------------------------------------------------------------
# handle_list_nodes
# ---------------------------------------------------------------------------

async def test_handle_list_nodes_success():
    with patch("k8s_mcp.tools.awareness.kubectl", return_value="minikube   Ready"):
        result = await handle_list_nodes({})
    assert "minikube" in result[0].text


async def test_handle_list_nodes_error():
    with patch("k8s_mcp.tools.awareness.kubectl", side_effect=KubectlError("no nodes")):
        result = await handle_list_nodes({})
    assert "Error" in result[0].text


# ---------------------------------------------------------------------------
# handle_list_pods
# ---------------------------------------------------------------------------

async def test_handle_list_pods_no_args():
    with patch("k8s_mcp.tools.awareness.kubectl", return_value="pod-abc   Running") as mock_kctl:
        await handle_list_pods({})
    cmd = mock_kctl.call_args[0][0]
    assert cmd[:2] == ["get", "pods"]
    assert mock_kctl.call_args.kwargs.get("namespace") is None
    assert mock_kctl.call_args.kwargs.get("all_namespaces") is False


async def test_handle_list_pods_with_namespace():
    with patch("k8s_mcp.tools.awareness.kubectl", return_value="pod   Running") as mock_kctl:
        await handle_list_pods({"namespace": "kube-system"})
    assert mock_kctl.call_args.kwargs["namespace"] == "kube-system"


async def test_handle_list_pods_all_namespaces():
    with patch("k8s_mcp.tools.awareness.kubectl", return_value="pod   Running") as mock_kctl:
        await handle_list_pods({"all_namespaces": True})
    assert mock_kctl.call_args.kwargs["all_namespaces"] is True


async def test_handle_list_pods_label_selector():
    with patch("k8s_mcp.tools.awareness.kubectl", return_value="pod   Running") as mock_kctl:
        await handle_list_pods({"label_selector": "app=nginx"})
    cmd = mock_kctl.call_args[0][0]
    assert "-l" in cmd
    assert "app=nginx" in cmd


async def test_handle_list_pods_error():
    with patch("k8s_mcp.tools.awareness.kubectl", side_effect=KubectlError("forbidden")):
        result = await handle_list_pods({})
    assert "Error" in result[0].text


# ---------------------------------------------------------------------------
# handle_list_deployments
# ---------------------------------------------------------------------------

async def test_handle_list_deployments_success():
    with patch("k8s_mcp.tools.awareness.kubectl", return_value="app   3/3") as mock_kctl:
        result = await handle_list_deployments({"namespace": "default"})
    assert mock_kctl.call_args.kwargs["namespace"] == "default"
    assert "3/3" in result[0].text


async def test_handle_list_deployments_all_namespaces():
    with patch("k8s_mcp.tools.awareness.kubectl", return_value="app   3/3") as mock_kctl:
        await handle_list_deployments({"all_namespaces": True})
    assert mock_kctl.call_args.kwargs["all_namespaces"] is True


# ---------------------------------------------------------------------------
# handle_list_services
# ---------------------------------------------------------------------------

async def test_handle_list_services_success():
    with patch("k8s_mcp.tools.awareness.kubectl", return_value="svc   ClusterIP") as mock_kctl:
        result = await handle_list_services({"namespace": "default"})
    assert "ClusterIP" in result[0].text


# ---------------------------------------------------------------------------
# handle_list_events — includes regression test for all_namespaces default bug
# ---------------------------------------------------------------------------

async def test_handle_list_events_defaults_all_namespaces_false():
    """Regression: calling with a namespace should NOT set all_namespaces=True."""
    with patch("k8s_mcp.tools.awareness.kubectl", return_value="events output") as mock_kctl:
        await handle_list_events({"namespace": "default"})
    assert mock_kctl.call_args.kwargs.get("all_namespaces") is False


async def test_handle_list_events_explicit_all_namespaces():
    with patch("k8s_mcp.tools.awareness.kubectl", return_value="events") as mock_kctl:
        await handle_list_events({"all_namespaces": True})
    assert mock_kctl.call_args.kwargs["all_namespaces"] is True


async def test_handle_list_events_warnings_only():
    with patch("k8s_mcp.tools.awareness.kubectl", return_value="warning events") as mock_kctl:
        await handle_list_events({"warnings_only": True})
    cmd = mock_kctl.call_args[0][0]
    assert any("Warning" in arg for arg in cmd)


async def test_handle_list_events_no_filter_by_default():
    with patch("k8s_mcp.tools.awareness.kubectl", return_value="all events") as mock_kctl:
        await handle_list_events({})
    cmd = mock_kctl.call_args[0][0]
    assert not any("Warning" in arg for arg in cmd)


# ---------------------------------------------------------------------------
# handle_list_images
# ---------------------------------------------------------------------------

IMAGES_OUTPUT = (
    "NAMESPACE     POD                  CONTAINER   IMAGE\n"
    "kube-system   coredns-abc          coredns     registry.k8s.io/coredns/coredns:v1.10.1\n"
    "default       myapp-xyz            app         nginx:1.25\n"
)


async def test_handle_list_images_success():
    with patch("k8s_mcp.tools.awareness.kubectl", return_value=IMAGES_OUTPUT):
        result = await handle_list_images({})
    assert len(result) == 1
    assert isinstance(result[0], TextContent)
    text = result[0].text
    assert "IMAGE" in text
    assert "nginx:1.25" in text
    assert "coredns" in text


async def test_handle_list_images_uses_custom_columns():
    with patch("k8s_mcp.tools.awareness.kubectl", return_value=IMAGES_OUTPUT) as mock_kctl:
        await handle_list_images({})
    cmd = mock_kctl.call_args[0][0]
    assert "get" in cmd
    assert "pods" in cmd
    assert any("custom-columns" in arg for arg in cmd)


async def test_handle_list_images_with_namespace():
    with patch("k8s_mcp.tools.awareness.kubectl", return_value=IMAGES_OUTPUT) as mock_kctl:
        await handle_list_images({"namespace": "kube-system"})
    assert mock_kctl.call_args.kwargs["namespace"] == "kube-system"
    assert mock_kctl.call_args.kwargs["all_namespaces"] is False


async def test_handle_list_images_all_namespaces():
    with patch("k8s_mcp.tools.awareness.kubectl", return_value=IMAGES_OUTPUT) as mock_kctl:
        await handle_list_images({"all_namespaces": True})
    assert mock_kctl.call_args.kwargs["all_namespaces"] is True


async def test_handle_list_images_passes_context():
    with patch("k8s_mcp.tools.awareness.kubectl", return_value=IMAGES_OUTPUT) as mock_kctl:
        await handle_list_images({"context": "prod"})
    assert mock_kctl.call_args.kwargs["context"] == "prod"


async def test_handle_list_images_error():
    with patch("k8s_mcp.tools.awareness.kubectl", side_effect=KubectlError("forbidden")):
        result = await handle_list_images({})
    assert "Error" in result[0].text
    assert "forbidden" in result[0].text
